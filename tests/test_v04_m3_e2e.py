"""Whole-stack witnesses for the three M3 feedback hooks, over a real routed graph.

Everything here runs the shipped scheduler: the M2 routed-graph fixture, the real routing
service over a ``MemoryStore``, the real ``DefaultFeedbackPipeline``, real approvals and a real
git worktree. The only substitutions are at the two edges a test has to be able to state — what
a verifier decided (``ScriptedVerifier``) and what the runtime reports about itself
(``DriftingRuntime``) — and both are hand written with call counters rather than mocked.

The three claims, and why each needs a whole run rather than a unit test:

* **A material verifier conflict blocks acceptance until it is resolved (AC4-M3-027).** The
  conflict is between *records*, so it only exists once two independent verdicts about one
  execution instance have been sealed, which only happens inside a run.
* **A configuration failure reroutes without touching the graph.** That the graph is untouched
  is a statement about ``list_graph_revisions`` and about the second receipt naming the first
  configuration as refused — neither is observable from the router alone.
* **A structural failure replans without widening the router's authority.** The authority
  invariant is that the policy snapshot and the required capability set do not move, and both
  live on documents the run wrote.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_v04_m2_end_to_end import (
    RoutedGraphFixture,
    _routed_graph,
    _wait_for_approval,
    _write_valid_output,
)
from test_v04_m2_service import _routable_execution
from test_v04_m5_coldstart import (
    _evidence_template,
    _seeded_candidate,
    _seeded_context,
    _service,
    install_router,
    learned_once,
)

from accretion.contracts import (
    ApprovalDecisionValue,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    RunState,
    RuntimeHealth,
    SessionConfig,
    SessionRef,
    TaskType,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
)
from accretion.contracts.routing import (
    ConstructionStage,
    DecisionType,
    ExperienceRecord,
    FailureOwner,
    FailureType,
    RouterModelVersion,
    RouterScope,
    VerificationState,
)
from accretion.experience.models import (
    Experience,
    ExperienceDetail,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.feedback.evidence import StoreEvidenceRetriever
from accretion.feedback.experience import EXPERIENCE_ID_LABEL
from accretion.feedback.service import DefaultFeedbackPipeline, as_feedback_pipeline
from accretion.ids import derived_id, new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.coldstart import ColdStartScorer
from accretion.routing.identity import workspace_for_run
from accretion.routing.protocols import RoutingMode
from accretion.routing.stages import (
    ADAPTER_UNAVAILABLE,
    DEGRADED_LABEL,
    node_signature,
)
from accretion.routing.train import LearnedPredictorLoader
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.verifiers.registry import VerifierRegistry
from accretion.verifiers.results import verification_result

OPERATOR = PrincipalRef(
    principal_id="usr_M3E2EOPERATOR00000000000000",
    display_name="v0.4 M3 end-to-end operator",
    status=PrincipalStatus.ACTIVE,
)
TEMPLATE_VERIFIERS = ("git-diff", "output-contract", "trajectory-policy")
DIGEST = "d" * 64


class ScriptedVerifier:
    """A registered verifier whose verdicts the test states, one call at a time.

    Registered under one of the template's own verifier ids rather than a new one, because the
    acceptance policy is built from the template and the frozen verification spec has one
    REQUIRED claim per *required verifier*: a verifier under a new id would grade nothing the
    spec asks about, and the disagreement this file is about would be invisible.

    The evidence ref is content-addressed the way the shipped verifiers address theirs, which
    is what makes the claim *covered*. An opaque ref would leave every claim uncovered and each
    verdict would come back INCONCLUSIVE — a green test proving nothing.
    """

    def __init__(self, verifier_id: str, statuses: Sequence[VerificationStatus]) -> None:
        self.verifier_id = verifier_id
        self.verifier_version = f"{verifier_id}-scripted-v1"
        self.statuses = deque(statuses)
        self.last = statuses[-1]
        self.calls = 0

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        del context
        self.calls += 1
        status = self.statuses.popleft() if self.statuses else self.last
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=time.monotonic(),
            evidence_refs=[f"{self.verifier_id}-sha256:{DIGEST}"],
        )


class DriftingRuntime(FakeRuntime):
    """A runtime that reports one drifted version, between one routing decision and its submit.

    The exact shape the pre-submission check exists for: the router observed one runtime and
    dispatch met another. Two properties are deliberate. The flip is armed by
    ``SessionConfig.model``, because only a *routed* session names the configuration's model,
    so the drift lands inside the first routed node rather than at an arbitrary call count. And
    it is reported exactly *once*: a runtime that stayed drifted would make the next attempt
    build its candidates from the new version, every configuration signature would move, and
    the §9.7 rule under test — an equivalent failed configuration is not retried without new
    evidence — would be unobservable because no equivalent configuration would still exist.
    """

    def __init__(self) -> None:
        super().__init__(
            scripted_outcomes=[
                FakeCallOutcome(),
                FakeCallOutcome(hook=_write_valid_output),
            ]
        )
        self.armed = False
        self.drifts = 0

    async def create_session(self, config: SessionConfig) -> SessionRef:
        session = await super().create_session(config)
        if config.model is not None and self.drifts == 0:
            self.armed = True
        return session

    async def health(self) -> RuntimeHealth:
        health = await super().health()
        if not self.armed:
            return health
        self.armed = False
        self.drifts += 1
        return health.model_copy(update={"runtime_version": "fake-p2-drifted"})


class SeedingMaterializer:
    """``ExperienceService.materialize`` reduced to the row the projection is keyed by.

    The real service derives a P7 experience from the run's trajectory, needs a git repository
    and is behind a feature gate; the projector needs exactly one thing from it — an existing
    ``experiences`` row for this run — and the ``RESTRICT`` key both stores mirror is what makes
    a fake that skipped the row accept writes PostgreSQL would refuse. One experience per run,
    cached, because a materializer that minted a fresh one per node would file each node of a
    run under a different experience.
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.calls: list[str] = []
        self.experiences: dict[str, ExperienceDetail] = {}

    async def materialize(
        self, run_id: str, *, candidate_id: str | None = None
    ) -> ExperienceDetail:
        del candidate_id
        self.calls.append(run_id)
        if run_id in self.experiences:
            return self.experiences[run_id]
        run = await self.store.get_run(run_id)
        assert run is not None
        experience = Experience(
            experience_id=new_id("experience"),
            project_id=run.project_id,
            repository_identity=DIGEST,
            task_id=run.task_id,
            task_type=TaskType.OTHER,
            task_family="m3-end-to-end",
            source_kind=ExperienceSourceKind.RUN,
            source_run_id=run_id,
            source_commit="c" * 40,
            architecture_version="2.0",
            manifest_digest=DIGEST,
            policy_digest=DIGEST,
            verifier_digest=DIGEST,
            prompt_digest=DIGEST,
            context_digest=DIGEST,
            tool_profile_digest=DIGEST,
            provider=Provider.FAKE,
            runtime_model="fake-model",
            runtime_version="fake-p2-v1",
            trust=ExperienceTrust.HIGH,
            polarity=ExperiencePolarity.POSITIVE,
            outcome="VERIFIED_SUCCESS",
            content_digest=DIGEST,
        )
        segment = TrajectorySegment(
            segment_id=new_id("trajectory_segment"),
            experience_id=experience.experience_id,
            ordinal=1,
            kind=TrajectorySegmentKind.WORKFLOW_PATH,
            content={"run": run_id},
            content_digest=DIGEST,
        )
        embedding = ExperienceEmbedding(
            embedding_id=new_id("experience_embedding"),
            experience_id=experience.experience_id,
            input_digest=DIGEST,
            vector=[1.0] + [0.0] * 383,
        )
        await self.store.save_experience(experience, (segment,), embedding)
        detail = ExperienceDetail(
            experience=experience,
            segments=[segment],
            embedding_version="v1",
            embedding_input_digest=DIGEST,
        )
        self.experiences[run_id] = detail
        return detail


async def setup_feedback_run(
    tmp_path: Path,
    *,
    runtime_factory,  # type: ignore[no-untyped-def]
    verifiers: Sequence[ScriptedVerifier] | None = None,
    attempts: int = 1,
) -> tuple[RoutedGraphFixture, SeedingMaterializer]:
    """The M2 routed graph with M3's pipeline attached, and nothing else changed.

    The fixture is imported rather than rebuilt so that these tests execute the same graph,
    the same template and the same approvals M2's witnesses do: a private copy would drift and
    the drift would be invisible, because both copies would go on passing.

    ``attempts`` raises the task's loop budget, and it has to be stated rather than assumed:
    a node contract's ``resource_cap.maximum_attempts`` is
    ``min(max_loop_iterations, max_node_retries + 1)`` and ``TaskBudgets`` defaults the first
    to one, so with the default budget §9.7's hard cap stops recovery before any decision it
    makes can be observed. A test that did not raise it would be testing the cap.
    """

    store = MemoryStore()
    fixture = await _routed_graph(tmp_path, store=store, runtime_factory=runtime_factory)
    if attempts > 1:
        task = await store.get_task(fixture.task_id)
        assert task is not None
        budgeted = await fixture.manager.create_task(
            project_id=task.envelope.project_id,
            objective=task.envelope.objective,
            task_patch={
                "task_type": "OTHER",
                "risk_level": "HIGH",
                "required_outputs": [{"path": "result.json", "kind": "json"}],
                "budgets": {"max_loop_iterations": attempts},
            },
        )
        fixture = RoutedGraphFixture(
            manager=fixture.manager,
            store=fixture.store,
            runtime=fixture.runtime,
            task_id=budgeted.envelope.task_id,
            principal_id=fixture.principal_id,
            workspace_id=fixture.workspace_id,
        )
    if verifiers is not None:
        fixture.manager.verifiers = VerifierRegistry(verifiers)
    materializer = SeedingMaterializer(store)
    fixture.manager.feedback_pipeline = as_feedback_pipeline(
        DefaultFeedbackPipeline(store, materializer, lambda: datetime.now(UTC), OPERATOR)
    )
    return fixture, materializer


def scripted_registry(
    disagreement: VerificationStatus, *, agreeing: VerificationStatus = VerificationStatus.PASS
) -> list[ScriptedVerifier]:
    """The three template verifiers, one of which disagrees on its first call only."""

    return [
        ScriptedVerifier("git-diff", [agreeing]),
        ScriptedVerifier("output-contract", [disagreement, VerificationStatus.PASS]),
        ScriptedVerifier("trajectory-policy", [agreeing]),
    ]


async def drive_to_completion(manager: RunManager, run_id: str, approvals: int) -> None:
    """Resolve ``approvals`` gates as they appear and wait for the scheduler to stop."""

    background = manager.background[run_id]
    for _ in range(approvals):
        approval = await _wait_for_approval(manager, run_id)
        await manager.resolve_approval(approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(background, 20)


async def node_contracts(fixture: RoutedGraphFixture, run_id: str, node_key: str) -> list:
    """Every frozen contract for one logical node of one run, oldest attempt first."""

    contracts = [
        contract
        for contract in await fixture.store.list_node_contracts(
            workspace_id=fixture.workspace_id
        )
        if contract.labels.get("run_id") == run_id
        and contract.labels.get("node_key") == node_key
    ]
    return sorted(contracts, key=lambda contract: int(contract.labels["attempt"]))


@pytest.mark.acceptance("AC4-M3-027")
async def test_a_material_verifier_conflict_pauses_the_run_until_it_is_adjudicated(
    tmp_path: Path,
) -> None:
    """AC4-M3-027, end to end: PASS against FAIL on a REQUIRED claim blocks acceptance.

    Three things are asserted and each kills a different mistake. The node is ``WAITING`` and
    the run is ``PAUSED`` rather than ``FAILED``, which is what dies if the conflict is treated
    as a settled failure — the reading the recorder refuses, because the record is evidence
    against its own failing side. Resuming *without* adjudicating pauses again, which is what
    dies if the block is a one-shot rather than a condition. And only after the contradiction
    is resolved does the run reach acceptance, which is the "until resolved" half.
    """

    fixture, _materializer = await setup_feedback_run(
        tmp_path,
        runtime_factory=lambda _store, _workspace: FakeRuntime(
            scripted_outcomes=[FakeCallOutcome(), FakeCallOutcome(hook=_write_valid_output)]
        ),
        verifiers=scripted_registry(VerificationStatus.FAIL),
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    background = manager.background[run.run_id]
    approval = await _wait_for_approval(manager, run.run_id)
    await manager.resolve_approval(approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(background, 20)

    paused = await fixture.store.get_run(run.run_id)
    assert paused is not None and paused.state is RunState.PAUSED
    records = await fixture.store.list_verification_results(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    conflicted = [record for record in records if record.conflict_refs]
    assert conflicted, [record.status for record in records]
    assert {record.status for record in conflicted} == {VerificationState.INCONCLUSIVE}
    instance = conflicted[0].execution_instance_id
    assert instance in {
        contract.execution_instance_id
        for contract in await node_contracts(fixture, run.run_id, "act")
    }
    # The node's own status is durable as its exit event: a checkpoint is written at edge
    # boundaries, and this node never reached one.
    exits = [
        event
        for event in await fixture.store.list_events(run.run_id)
        if event.native_type == "accretion/node-exited"
        and event.node_id == f"{run.run_id}:verify"
    ]
    assert [event.payload["status"] for event in exits] == ["WAITING"]

    # Resuming an unadjudicated contradiction pauses again: the block is the state of the
    # evidence, not a flag the first pause consumed.
    await manager.resume(run.run_id)
    await asyncio.wait_for(manager.background[run.run_id], 20)
    still_paused = await fixture.store.get_run(run.run_id)
    assert still_paused is not None and still_paused.state is RunState.PAUSED

    await manager.resolve_verification_contradiction(
        run.run_id,
        instance,
        resolution="the output-contract verifier ran against a stale worktree",
    )
    await manager.resume(run.run_id)
    await drive_to_completion(manager, run.run_id, approvals=1)

    finished = await fixture.store.get_run(run.run_id)
    assert finished is not None and finished.state is RunState.SUCCEEDED
    # The contradiction is settled, not erased: both verdicts are still readable.
    assert [
        record.contract_id
        for record in await fixture.store.list_verification_results(
            workspace_id=fixture.workspace_id, project_id=run.project_id
        )
        if record.conflict_refs
    ] == [record.contract_id for record in conflicted]


async def test_an_immaterial_verifier_disagreement_does_not_pause_the_run(
    tmp_path: Path,
) -> None:
    """The other half of "material": a verdict that declined to decide is not a contradiction.

    ``INCONCLUSIVE`` beside ``PASS`` is one verifier saying it could not tell, which registry
    §5.1 makes blocking for *acceptance* and which §7.9 does not make a conflict. Without this
    test the pause above would be satisfied by an implementation that stopped the run on any
    disagreement at all, and every inconclusive check would deadlock a run.
    """

    fixture, _materializer = await setup_feedback_run(
        tmp_path,
        runtime_factory=lambda _store, _workspace: FakeRuntime(
            scripted_outcomes=[FakeCallOutcome(), FakeCallOutcome(hook=_write_valid_output)]
        ),
        verifiers=scripted_registry(VerificationStatus.INCONCLUSIVE),
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    background = manager.background[run.run_id]
    approval = await _wait_for_approval(manager, run.run_id)
    await manager.resolve_approval(approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(background, 20)

    finished = await fixture.store.get_run(run.run_id)
    assert finished is not None
    assert finished.state is not RunState.PAUSED
    records = await fixture.store.list_verification_results(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    assert records
    assert all(not record.conflict_refs for record in records)


async def test_a_runtime_drift_reroutes_the_node_without_replanning_the_graph(
    tmp_path: Path,
) -> None:
    """E2E #5: a CONFIGURATION failure spends a new attempt, not a new graph revision.

    Four things are asserted and the interesting one is the second receipt's refusal list.
    §9.7's rule is that an equivalent failed configuration is not retried without new evidence,
    and the only place that rule is observable is the candidate the second decision *rejected*;
    a router that merely re-ranked would produce the same selection with an empty refusal list
    and nothing else in the run would look different.

    The run ends ``REQUIRES_HUMAN`` and that is the point rather than a shortcoming. Under
    ``BASELINE_ONLY`` the only selectable configuration is the audited baseline, so once it has
    been refused there is nothing left this router is authorised to choose, and §9.7's answer is
    to escalate rather than to repeat the configuration that failed. An implementation that
    ignored the exclusion would instead sail through to a green run, which is exactly the silent
    repeat AC4-M3-032 forbids.

    The attempt bookkeeping is asserted through the execution instance ids because ADR-041 makes
    a retry a different routable action — same node, different identity — and reusing the first
    identity would attribute the retry's outcome to the configuration that failed.
    """

    fixture, _materializer = await setup_feedback_run(
        tmp_path,
        runtime_factory=lambda _store, _workspace: DriftingRuntime(),
        attempts=3,
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    await asyncio.wait_for(manager.background[run.run_id], 20)

    finished = await fixture.store.get_run(run.run_id)
    assert finished is not None and finished.state is RunState.REQUIRES_HUMAN
    runtime = fixture.runtime
    assert isinstance(runtime, DriftingRuntime) and runtime.drifts == 1

    failures = await fixture.store.list_failure_events(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    assert [failure.failure_type for failure in failures] == [FailureType.CONFIGURATION]
    assert failures[0].assigned_owner is FailureOwner.CONFIGURATION
    assert failures[0].retryable

    attempts = await node_contracts(fixture, run.run_id, "plan")
    assert [contract.labels["attempt"] for contract in attempts] == ["1", "2"]
    assert (
        attempts[0].execution_instance_id != attempts[1].execution_instance_id
    ), "a retry must not reuse the identity of the attempt that failed"

    receipts = [
        receipt
        for receipt in await fixture.store.list_routing_receipts(
            workspace_id=fixture.workspace_id, project_id=run.project_id
        )
        if receipt.node_contract_hash
        in {contract.immutable_hash for contract in attempts}
    ]
    assert len(receipts) == 2
    first, second = receipts
    refused = {
        rejected.candidate_id: rejected
        for rejected in second.rejected_candidate_reasons
        if rejected.reason_code == "ATTEMPTED_WITHOUT_NEW_EVIDENCE"
    }
    excluded_id = derived_id(
        "configuration_candidate",
        second.routing_request_id,
        first.selected_configuration_hash or "",
    )
    assert excluded_id in refused
    assert refused[excluded_id].stage is ConstructionStage.CONSTRUCT_TUPLE
    assert second.decision_type is DecisionType.HUMAN_REVIEW_REQUIRED
    assert second.selected_configuration_hash is None

    # The router chose again; nobody replanned, so no graph revision was recorded.
    assert await fixture.store.list_graph_revisions(run.run_id) == []


async def test_a_structural_failure_replans_without_widening_the_routers_authority(
    tmp_path: Path,
) -> None:
    """E2E #6: findings against required output claims are the planner's problem, not the router's.

    The taxonomy has to type this as ``STRUCTURAL`` rather than as another configuration
    attempt — that is AC4-M3-030's whole content — and the recovery has to stay inside the
    graph's own repair edge. The authority invariants are asserted on the documents the run
    wrote: every decision names one policy snapshot, and no later attempt requires a capability
    an earlier one did not.
    """

    fixture, _materializer = await setup_feedback_run(
        tmp_path,
        runtime_factory=lambda _store, _workspace: FakeRuntime(
            scripted_outcomes=[FakeCallOutcome(), FakeCallOutcome(hook=_write_valid_output)]
        ),
        verifiers=[
            ScriptedVerifier(verifier_id, [VerificationStatus.FAIL])
            for verifier_id in TEMPLATE_VERIFIERS
        ],
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    await drive_to_completion(manager, run.run_id, approvals=1)

    finished = await fixture.store.get_run(run.run_id)
    assert finished is not None
    assert finished.state in {RunState.FAILED, RunState.REQUIRES_HUMAN}

    failures = await fixture.store.list_failure_events(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    assert failures
    assert {failure.failure_type for failure in failures} == {FailureType.STRUCTURAL}
    assert {failure.assigned_owner for failure in failures} == {FailureOwner.STRUCTURAL}
    assert {failure.affected_layer for failure in failures} == {"plan-graph"}

    # The repair edge the template declares was taken, and the graph itself was not rewritten:
    # a replan writes a graph revision row, and this recovery wrote none.
    assert len(await node_contracts(fixture, run.run_id, "act")) == 2
    assert await fixture.store.list_graph_revisions(run.run_id) == []

    receipts = await fixture.store.list_routing_receipts(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    assert len({receipt.policy_snapshot_id for receipt in receipts}) == 1
    contracts = await node_contracts(fixture, run.run_id, "act")
    first = {
        (requirement.capability.capability_id, requirement.required_scope)
        for requirement in contracts[0].required_capabilities
    }
    later = {
        (requirement.capability.capability_id, requirement.required_scope)
        for requirement in contracts[1].required_capabilities
    }
    assert later <= first


async def test_the_projection_of_a_finished_run_is_written_once_per_routed_node(
    tmp_path: Path,
) -> None:
    """ADR-048's ordering, observed where it is decided: at the run terminal and nowhere else.

    Projecting at node completion would put outcomes from runs that were later thrown away
    into the training set, so what this asserts is the *count* and the *timing*: one record per
    node that a router actually decided about, each carrying the run's grade, and none of them
    written before the run reached its terminal state.
    """

    fixture, materializer = await setup_feedback_run(
        tmp_path,
        runtime_factory=lambda _store, _workspace: FakeRuntime(
            scripted_outcomes=[FakeCallOutcome(), FakeCallOutcome(hook=_write_valid_output)]
        ),
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    await drive_to_completion(manager, run.run_id, approvals=2)

    finished = await fixture.store.get_run(run.run_id)
    assert finished is not None and finished.state is RunState.SUCCEEDED
    workspace_id = await workspace_for_run(fixture.store, finished)
    receipts = await fixture.store.list_routing_receipts(
        workspace_id=workspace_id, project_id=run.project_id
    )
    records = await fixture.store.list_experience_records(
        workspace_id=workspace_id, project_id=run.project_id
    )
    assert len(records) == len(receipts)
    assert {record.final_run_status for record in records} == {VerificationState.PASS}
    assert {record.created_by.principal_id for record in records} == {
        fixture.principal_id
    }
    # One experience per run, and it is the run's: a materializer called once per node that
    # minted a fresh row each time would file one run under several experiences.
    assert len(materializer.experiences) == 1
    assert set(materializer.calls) == {run.run_id}


async def test_stored_experience_reaches_an_auto_routed_receipt(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """E2E #2: the records this milestone writes are the records the next decision cites.

    The two halves of M3 meet here. ``record_final`` projects experience records; the
    store-backed retriever is what makes them reachable; and the receipt's ``experience_refs``
    is where a decision says which of them moved it. Nothing between the two is stubbed — the
    retriever reads the same rows the projector wrote, under the same signature — so a
    retriever keyed on anything but §7.10's signature would cite nothing here and the
    ``AUTO`` receipt would be indistinguishable from a cold start.

    ``project_adapter_version`` is asserted beside it because §8.3 makes a decision
    attributable to the *pair* of models that made it, and the adapter is the half a
    project-local deployment actually tunes.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    store = execution.store
    node = execution.frozen.node_contract
    workspace_id = node.workspace_id
    project_id = node.project_id
    assert project_id is not None
    prior = await install_router(store, workspace_id, learned)
    # The project adapter is the same trained bytes re-tenanted at project scope: §13.1 allows
    # one ACTIVE row per scope, and installing a second *workspace* router would be refused.
    adapter_payload = prior.model_dump(mode="python")
    adapter_payload.update(
        contract_id=new_id("router_model_version"),
        scope=RouterScope.PROJECT_ADAPTER,
        project_id=project_id,
        content_hash="",
    )
    adapter = await store.put_router_model_version(
        RouterModelVersion.model_validate(adapter_payload)
    )

    signature = node_signature(
        node, objective_digest=execution.frozen.objective_ref.objective_contract_hash
    )
    context = await _seeded_context(execution)
    candidate = await _seeded_candidate(execution, context)
    template = _evidence_template(node, candidate, signature)
    detail = await SeedingMaterializer(store).materialize(execution.run.run_id)
    seeded = []
    for _ in range(3):
        payload = template.model_dump(mode="python")
        payload.update(
            contract_id=new_id("experience"),
            content_hash="",
            project_id=project_id,
            labels={EXPERIENCE_ID_LABEL: detail.experience.experience_id},
        )
        seeded.append(
            await store.put_experience_record(
                ExperienceRecord.model_validate(payload),
                experience_id=detail.experience.experience_id,
            )
        )

    receipt = await _service(
        execution,
        evidence=StoreEvidenceRetriever(store),
        scorer=ColdStartScorer(
            LearnedPredictorLoader(store, learned.artifacts),
            learned.artifacts,
            lambda: datetime(2026, 5, 1, tzinfo=UTC),
        ),
    ).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert sorted(stored.experience_refs) == sorted(
        record.contract_id for record in seeded
    )
    cited = await store.get_routing_request(stored.routing_request_id)
    assert cited is not None
    assert cited.project_features.observed_task_count == len(seeded)
    # §8.3's two halves, and they are deliberately different statements. The routing *context*
    # names the adapter that was in force; the *receipt* attributes the decision, and this
    # adapter's artefact is the workspace prior's rather than a fitted per-project one, so the
    # scorer declined it and said so on the ladder. Asserting the receipt named it anyway would
    # be asserting that a model which never ran is answerable for the outcome.
    assert cited.project_adapter_version == adapter.contract_id
    assert stored.labels[DEGRADED_LABEL] == ADAPTER_UNAVAILABLE
    assert stored.project_adapter_version is None
    assert stored.workspace_router_version == prior.contract_id
