"""The whole ``FeedbackPipeline``, driven store-only: local, final, classify, recover.

The four methods are tested as the lifecycle they are, because the interesting failures are all
between them rather than inside one:

**Two records, two tables, two questions.** ``AC4-M3-025``. ``record_local`` writes a §7.9
independent verification result and ``record_final`` writes a §7.10 experience record, and each
answers a question the other cannot: "what did the verifier decide about this node" against "what
may a router learn from this run". A node that passed inside a run that failed makes them differ,
and that is the case the test drives.

**Independence is structural and produces ERROR, not a footnote.** A verifier declared to have run
in the producer's session voids the verdict. The pipeline is the layer that decides which sessions
to declare, so the refusal has to be reachable through it and not only through the recorder.

**Recovery derives what the Protocol does not carry.** The guard needs an attempt number, an
attempted set, new evidence per hash and a prior success rate; ``recovery_decision`` receives none
of them and takes all four out of the store. The tests pin the derivations, not just the verdict:
a configuration failure reselects to a hash *outside* the attempted set, and a second distinct
configuration failure on one node re-types to STRUCTURAL and goes to the planner.

**Events are notifications, and the row is the record.** Both §12 events this milestone owns are
emitted only when the run exists, and the golden-trace rule means no event may appear on a path
that did not ask for one.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import (
    AcceptancePolicy,
    ErrorSummary,
    EventType,
    ExecutionMode,
    GraphEdgeKind,
    GraphNodeKind,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Run,
    RunEdge,
    RunGraph,
    RunNode,
    RunState,
    Task,
    TaskEnvelope,
    TaskType,
    TemplateStatus,
    VerificationResult,
    VerificationStatus,
    WorkflowNodeSpec,
    WorkflowTemplate,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ContradictionStatus,
    DecisionType,
    ExecutionConfiguration,
    ExperienceOutcomes,
    FailureOwner,
    FailureType,
    NodeContract,
    ResourceBudget,
    RoutingDecisionReceipt,
    VerificationSpec,
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
from accretion.feedback.attribution import METHOD_VERSION
from accretion.feedback.service import DefaultFeedbackPipeline, as_feedback_pipeline
from accretion.identity import LOCAL_WORKSPACE_ID
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.identity import execution_instance_id
from accretion.templates import compute_template_checksum

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
NODE_FIXTURE: dict[str, Any] = json.loads(
    (FIXTURE_ROOT / "node_contract" / "minimal.json").read_text(encoding="utf-8")
)
PROJECT_ID: str = NODE_FIXTURE["project_id"]
RUN_GRAPH_ID: str = NODE_FIXTURE["run_graph_id"]
WORKSPACE_ID = LOCAL_WORKSPACE_ID
"""The workspace ``workspace_for_run`` derives, and therefore the one every contract is filed in.

Derived rather than chosen: ``workspace_for_run`` reads the principal's memberships and falls back
to the local workspace, so a test that filed its contracts under the fixture's workspace id would
have the pipeline looking in an empty one — and would pass or fail for a reason that has nothing
to do with feedback.
"""

SEALED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M3 pipeline test",
    status=PrincipalStatus.ACTIVE,
)
OPERATOR = PrincipalRef(
    principal_id="usr_OPERATOR000000000000000000",
    display_name="v0.4 M3 deployment operator",
    status=PrincipalStatus.ACTIVE,
)
POLICY = AcceptancePolicy(policy_id="m3-acceptance", required_verifiers=["git-diff"])
PRODUCER_SESSION = "ses_producer000000000000000"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted into the workspace the pipeline will look in.

    The workspace is *overwritten* rather than defaulted, because the fixture already carries one
    and a ``setdefault`` would silently keep it — leaving every contract in a workspace
    ``workspace_for_run`` never reads and every lookup in this file empty.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document["workspace_id"] = overrides.get("workspace_id", WORKSPACE_ID)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


class FakeMaterializer:
    """``materialize`` with a call counter and a failure switch, and no P7 service behind it.

    The counter is what proves ``record_final`` materialises once per *run* and not once per node:
    a run has one experience, and a projector that asked per node would create nothing extra but
    would make the second node's write depend on a second git subprocess in production.
    """

    def __init__(self, detail: ExperienceDetail) -> None:
        self.detail = detail
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    async def materialize(
        self, run_id: str, *, candidate_id: str | None = None
    ) -> ExperienceDetail:
        self.calls.append(run_id)
        if self.fail_with is not None:
            raise self.fail_with
        return self.detail


class Scenario:
    """Everything one run of the pipeline needs, assembled once and reachable by name."""

    def __init__(
        self,
        store: MemoryStore,
        pipeline: DefaultFeedbackPipeline,
        materializer: FakeMaterializer,
        run: Run,
        task: Task,
        experience: Experience,
        nodes: dict[str, NodeContract],
        spec: VerificationSpec,
        configurations: dict[str, ExecutionConfiguration],
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.materializer = materializer
        self.run = run
        self.task = task
        self.experience = experience
        self.nodes = nodes
        self.spec = spec
        self.configurations = configurations


def make_spec() -> VerificationSpec:
    """One REQUIRED verifier claim and one REQUIRED output claim.

    Both are needed: the verifier claim is what a verdict is about, and the output claim is the
    only kind ``FailureSignals.schema_findings`` counts, so a spec with one of them could not
    distinguish the two.
    """

    return build(
        VerificationSpec,
        contract_id=new_id("verification_spec"),
        project_id=PROJECT_ID,
        claims=[
            {
                "claim_id": "verifier.git-diff",
                "description": "the diff verifier accepts this node's work",
                "criticality": "REQUIRED",
                "required_evidence_types": ["DIGITAL"],
            },
            {
                "claim_id": "output.migrations/0021.py",
                "description": "the required migration file is present and well formed",
                "criticality": "REQUIRED",
                "required_evidence_types": ["DIGITAL"],
            },
        ],
    )


def make_configuration(marker: str) -> ExecutionConfiguration:
    document: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "execution_configuration" / "minimal.json").read_text(
            encoding="utf-8"
        )
    )
    document["environment"]["environment"]["policy_profile"] = f"profile-{marker}"
    document["contract_id"] = new_id("execution_configuration")
    document["workspace_id"] = WORKSPACE_ID
    document.pop("content_hash", None)
    document.pop("configuration_hash", None)
    return ExecutionConfiguration.model_validate(document)


def v01_result(
    verifier_id: str,
    status: VerificationStatus,
    run_id: str,
    *,
    evidence: tuple[str, ...] = (),
) -> VerificationResult:
    """A v0.1 verification result whose evidence refs are content-addressed the way verifiers do.

    ``<name>-sha256:<digest>`` is the shape ``verifiers/command.py`` and ``verifiers/git_diff.py``
    both emit, and it is the shape the pipeline's default evidence resolution understands. A test
    that invented an opaque ref would be testing the fail-closed path by accident.
    """

    return VerificationResult(
        verification_id=new_id("verification"),
        run_id=run_id,
        verifier_id=verifier_id,
        verifier_version="1.0.0",
        target_ref="workspace",
        status=status,
        evidence_refs=list(evidence),
        executed_at=SEALED_AT,
    )


async def setup_pipeline(*, node_keys: tuple[str, ...] = ("plan", "verify")) -> Scenario:
    """A store holding a project, a run, a graph, one node contract per key, and an experience.

    Assembled as a builder returning one object rather than a fixture, because every one of these
    rows is a *precondition* of the pipeline's writes — the project key, the experience key, the
    run the events need — and none of them is the subject of a test.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 M3 feedback pipeline",
            repository_path=Path("/tmp/accretion-v04-m3-pipeline"),
        )
    )
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id=WORKSPACE_ID, name="Local workspace")
    )
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=WORKSPACE_ID,
            principal_id=PRINCIPAL.principal_id,
            role=WorkspaceRole.ADMIN,
        )
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=PROJECT_ID,
            objective="Write the additive migration and prove it reverses.",
            task_type=TaskType.IMPLEMENT,
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
        principal_id=PRINCIPAL.principal_id,
        session_id=PRODUCER_SESSION,
    )
    await store.create_run(run)

    template = WorkflowTemplate(
        template_record_id="wft_01K4DQ9HVJXBQBN3YF83E5Y9TG",
        template_id="m3-pipeline",
        version="1.0.0",
        mode=ExecutionMode.GRAPH,
        nodes=[
            WorkflowNodeSpec(key=key, kind=GraphNodeKind.TASK, label=key)
            for key in node_keys
        ],
        checksum="pending",
        status=TemplateStatus.DRAFT,
    )
    template = template.model_copy(
        update={
            "checksum": compute_template_checksum(template),
            "status": TemplateStatus.VALIDATED,
        }
    )
    await store.upsert_workflow_template(template)
    await store.create_run_graph(
        RunGraph(
            run_graph_id=RUN_GRAPH_ID,
            run_id=run.run_id,
            task_id=task.envelope.task_id,
            template_record_id=template.template_record_id,
            template_id=template.template_id,
            template_version=template.version,
            template_checksum=template.checksum,
            nodes=[
                RunNode(node_id=key, key=key, kind=GraphNodeKind.TASK, label=key)
                for key in node_keys
            ],
            edges=[
                RunEdge(
                    edge_id=f"{left}-{right}",
                    key=f"{left}-{right}",
                    source=left,
                    target=right,
                    kind=GraphEdgeKind.NORMAL,
                )
                for left, right in zip(node_keys, node_keys[1:], strict=False)
            ],
        )
    )

    spec = make_spec()
    await store.put_verification_spec(spec)

    nodes: dict[str, NodeContract] = {}
    configurations: dict[str, ExecutionConfiguration] = {}
    for key in node_keys:
        node = build(
            NodeContract,
            contract_id=new_id("node_contract"),
            project_id=PROJECT_ID,
            node_id=key,
            objective=f"do the {key} work",
            run_graph_id=RUN_GRAPH_ID,
            execution_instance_id=execution_instance_id(run.run_id, key, 1),
            labels={"run_id": run.run_id, "node_key": key, "attempt": "1"},
            verification_spec_ref={
                "verification_spec_id": spec.contract_id,
                "content_hash": spec.content_hash,
            },
        )
        nodes[key] = await store.put_node_contract(node)
        configurations[key] = make_configuration(key)

    experience = Experience(
        experience_id=new_id("experience"),
        project_id=PROJECT_ID,
        repository_identity=digest(PROJECT_ID),
        task_id=task.envelope.task_id,
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=run.run_id,
        source_commit="b" * 40,
        architecture_version="2.0",
        manifest_digest=digest("manifest"),
        policy_digest=digest("policy"),
        verifier_digest=digest("verifier"),
        prompt_digest=digest("prompt"),
        context_digest=digest("context"),
        tool_profile_digest=digest("tools"),
        provider=Provider.FAKE,
        runtime_model="fake",
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest("experience"),
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience.experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": list(node_keys)},
        content_digest=digest("segment"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience.experience_id,
        input_digest=digest("embedding"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)

    materializer = FakeMaterializer(
        ExperienceDetail(
            experience=experience,
            segments=[segment],
            embedding_version="v1",
            embedding_input_digest=digest("embedding"),
        )
    )
    pipeline = DefaultFeedbackPipeline(
        store, materializer, lambda: SEALED_AT, OPERATOR
    )
    return Scenario(
        store, pipeline, materializer, run, task, experience, nodes, spec, configurations
    )


async def route(
    scenario: Scenario, key: str, *, created_at: datetime = SEALED_AT
) -> RoutingDecisionReceipt:
    """Persist the receipt and the candidate a routed node leaves behind.

    Both are needed and for different reasons: ``record_final`` finds the routed nodes through
    ``list_routing_receipts_for_run_graph`` and finds the *configuration* through the candidate
    that carries it, because a receipt names a configuration by id and stores no copy of it.
    """

    node = scenario.nodes[key]
    configuration = scenario.configurations[key]
    receipt = build(
        RoutingDecisionReceipt,
        contract_id=new_id("routing_receipt"),
        project_id=PROJECT_ID,
        routing_request_id=new_id("routing_request"),
        node_contract_hash=node.immutable_hash,
        decision_type=DecisionType.EXPLOIT.value,
        selected_configuration_id=configuration.contract_id,
        selected_configuration_hash=configuration.configuration_hash,
        created_at=created_at.isoformat(),
    )
    await scenario.store.put_routing_receipt(receipt)
    await scenario.store.put_configuration_candidate(
        build(
            ConfigurationCandidate,
            contract_id=new_id("configuration_candidate"),
            project_id=PROJECT_ID,
            routing_request_id=receipt.routing_request_id,
            configuration=configuration.model_dump(mode="json"),
        )
    )
    return receipt


# ------------------------------------------------------------------ record_local


async def test_the_pipeline_satisfies_the_frozen_feedback_protocol() -> None:
    """Structural conformance, asserted at runtime as well as by ``mypy src``.

    ``FeedbackPipeline`` is a Protocol, so nothing checks it unless something asks. The identity
    function this calls is annotated, which is what makes ``mypy src`` fail on a renamed keyword;
    this test is what makes an *import-time* break — a missing method — visible in the suite too.
    """

    scenario = await setup_pipeline()

    assert as_feedback_pipeline(scenario.pipeline) is scenario.pipeline


async def test_record_local_stores_a_verdict_that_names_the_spec_it_was_graded_against() -> None:
    """The coverage in the record is only worth reading because the digest agrees with it."""

    scenario = await setup_pipeline()

    result = await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=PRODUCER_SESSION,
        results=[
            v01_result(
                "git-diff",
                VerificationStatus.PASS,
                scenario.run.run_id,
                evidence=(f"git-diff-sha256:{digest('diff')}",),
            )
        ],
        policy=POLICY,
        configuration_hash=scenario.configurations["plan"].configuration_hash,
    )

    stored = await scenario.store.list_verification_results(workspace_id=WORKSPACE_ID)
    assert stored == [result]
    assert stored[0].verification_spec_hash == scenario.spec.content_hash
    assert {claim.claim_id for claim in stored[0].claim_results} == {
        "verifier.git-diff",
        "output.migrations/0021.py",
    }


@pytest.mark.acceptance("AC4-M3-003")
async def test_a_verifier_that_ran_in_the_producers_session_yields_error() -> None:
    """AC4-M3-003, second witness. OQ-418 is about context, so the comparison is on sessions.

    Declaring the producer's own session for the verifier is the structural violation, and it
    voids the verdict rather than annotating it: an ERROR is the absence of a judgement, and a
    self-accepted PASS is exactly the reward-hacking path §14.3 exists to close.
    """

    scenario = await setup_pipeline()

    result = await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=PRODUCER_SESSION,
        results=[
            v01_result(
                "git-diff",
                VerificationStatus.PASS,
                scenario.run.run_id,
                evidence=(f"git-diff-sha256:{digest('diff')}",),
            )
        ],
        policy=POLICY,
        configuration_hash=scenario.configurations["plan"].configuration_hash,
        verifier_session_ids={"git-diff": PRODUCER_SESSION},
    )

    stored = await scenario.store.get_verification_result(result.contract_id)
    assert stored is not None
    assert stored.status is VerificationState.ERROR


async def test_an_in_process_verifier_with_no_session_is_independent_by_default() -> None:
    """A verifier that never entered a session cannot have entered the producer's.

    The complement of the test above: without it, that one would pass on a pipeline that marked
    *every* verdict ERROR, which would be a different bug with the same assertion.
    """

    scenario = await setup_pipeline()

    result = await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=PRODUCER_SESSION,
        results=[
            v01_result(
                "git-diff",
                VerificationStatus.PASS,
                scenario.run.run_id,
                evidence=(f"git-diff-sha256:{digest('diff')}",),
            )
        ],
        policy=POLICY,
        configuration_hash=scenario.configurations["plan"].configuration_hash,
    )

    stored = await scenario.store.get_verification_result(result.contract_id)
    assert stored is not None
    assert stored.status is not VerificationState.ERROR


async def test_record_local_emits_one_event_naming_the_row_it_wrote() -> None:
    """A §12 notification about a durable record, not a substitute for one."""

    scenario = await setup_pipeline()

    result = await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=PRODUCER_SESSION,
        results=[v01_result("git-diff", VerificationStatus.PASS, scenario.run.run_id)],
        policy=POLICY,
        configuration_hash=scenario.configurations["plan"].configuration_hash,
    )

    events = await scenario.store.list_events(scenario.run.run_id)
    recorded = [
        event
        for event in events
        if event.normalized_type is EventType.VERIFICATION_RESULT_RECORDED
    ]
    assert len(recorded) == 1
    assert recorded[0].payload["verification_result_id"] == result.contract_id
    assert (
        recorded[0].payload["configuration_hash"]
        == scenario.configurations["plan"].configuration_hash
    )


async def test_an_unknown_execution_instance_is_refused_not_graded_against_a_guess() -> None:
    """A verdict recorded against a spec nobody froze would claim coverage of nothing."""

    scenario = await setup_pipeline()

    with pytest.raises(KeyError):
        await scenario.pipeline.record_local(
            run=scenario.run,
            task=scenario.task,
            execution_instance_id="exe_does_not_exist",
            session_id=PRODUCER_SESSION,
            results=[v01_result("git-diff", VerificationStatus.PASS, scenario.run.run_id)],
            policy=POLICY,
            configuration_hash=scenario.configurations["plan"].configuration_hash,
        )

    assert await scenario.store.list_verification_results(workspace_id=WORKSPACE_ID) == []


# ------------------------------------------------------------------ record_final


@pytest.mark.acceptance("AC4-M3-025")
async def test_the_local_verdict_and_the_final_projection_are_separate_records() -> None:
    """AC4-M3-025. Two tables, two queries, and two different answers for one node.

    The node's own verification **passed** and the run's final status is **FAIL**, so the two
    records genuinely disagree — which is the case that makes them impossible to collapse. A
    pipeline that stored one row would have to pick one of these two answers and lose the other.
    """

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await route(scenario, "verify")
    for key in ("plan", "verify"):
        await scenario.pipeline.record_local(
            run=scenario.run,
            task=scenario.task,
            execution_instance_id=scenario.nodes[key].execution_instance_id,
            session_id=PRODUCER_SESSION,
            results=[
                v01_result(
                    "git-diff",
                    VerificationStatus.PASS,
                    scenario.run.run_id,
                    evidence=(f"git-diff-sha256:{digest(key)}",),
                )
            ],
            policy=POLICY,
            configuration_hash=scenario.configurations[key].configuration_hash,
        )

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.FAIL,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    verdicts = await scenario.store.list_verification_results(workspace_id=WORKSPACE_ID)
    projections = await scenario.store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert len(verdicts) == 2
    assert len(projections) == 2
    # Compared as sets: the listing is ordered by ``(created_at, contract_id)`` and the return
    # value is in the order the nodes were routed, and neither order is the claim here.
    assert {record.contract_id for record in projections} == {
        record.contract_id for record in records
    }
    # The same node, two records, two different verdicts about two different questions.
    assert {verdict.status for verdict in verdicts} == {VerificationState.PASS}
    assert {record.final_run_status for record in projections} == {VerificationState.FAIL}
    assert {record.local_verification_status for record in projections} == {
        VerificationState.PASS
    }
    # Neither query can answer the other's question: the ids do not overlap at all.
    assert {verdict.contract_id for verdict in verdicts}.isdisjoint(
        {record.contract_id for record in projections}
    )


async def test_record_final_projects_one_record_per_routed_node_and_no_others() -> None:
    """A node that no router decided about is not evidence about a routing decision."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    assert [record.labels["node_key"] for record in records] == ["plan"]
    assert scenario.materializer.calls == [scenario.run.run_id]


async def test_every_projection_of_one_run_is_filed_under_that_runs_one_experience() -> None:
    """A run has one experience and many nodes, which is why the key had to move off the id."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await route(scenario, "verify")

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    chain = await scenario.store.list_experience_record_revisions(
        scenario.experience.experience_id, workspace_id=WORKSPACE_ID
    )
    assert sorted(record.contract_id for record in chain) == sorted(
        record.contract_id for record in records
    )
    assert len({record.contract_id for record in records}) == 2


async def test_the_first_generation_of_every_record_is_already_attributed() -> None:
    """Projecting unattributed and revising afterwards would leave a stale eligible row forever."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await route(scenario, "verify")

    await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    stored = await scenario.store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert {record.attribution.method_version for record in stored} == {METHOD_VERSION}
    assert all(record.supersedes_contract_id is None for record in stored)


async def test_a_recorded_failure_type_reaches_the_projection() -> None:
    """§7.11's typed taxonomy beside P7's free-string one, joined by the execution instance."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=[
            scenario.configurations["plan"].configuration_hash
        ],
    )

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.FAIL,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    assert records[0].failure_type is not None


async def test_measured_outcomes_reach_the_projection_unchanged() -> None:
    """The seam M3b fills; without it every stored cost would be zero."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
        outcomes={
            scenario.nodes["plan"].execution_instance_id: ExperienceOutcomes(
                quality=0.9, cost=Decimal("2.50"), latency_ms=1_234
            )
        },
    )

    assert records[0].outcomes.latency_ms == 1_234
    assert float(records[0].outcomes.cost) == 2.50


async def test_record_final_emits_one_experience_event_for_the_whole_run() -> None:
    """One run, one experience, one notification naming every row it produced."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await route(scenario, "verify")

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    created = [
        event
        for event in await scenario.store.list_events(scenario.run.run_id)
        if event.normalized_type is EventType.EXPERIENCE_CREATED
    ]
    assert len(created) == 1
    assert created[0].payload["experience_record_ids"] == [
        record.contract_id for record in records
    ]
    assert created[0].payload["source_kind"] == ExperienceSourceKind.RUN.value


async def test_a_run_with_no_routed_node_projects_nothing_and_emits_nothing() -> None:
    """Silence, not an empty event: a notification about no rows is a notification about nothing."""

    scenario = await setup_pipeline()

    records = await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    assert records == []
    assert await scenario.store.list_events(scenario.run.run_id) == []
    assert scenario.materializer.calls == []


# --------------------------------------------------------------- classify_failure


async def test_a_quarantined_verdict_is_owned_by_safety_and_is_not_retryable() -> None:
    """Governance first: no automatic recovery may look past an applied quarantine."""

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    local = await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=PRODUCER_SESSION,
        results=[v01_result("git-diff", VerificationStatus.PASS, scenario.run.run_id)],
        policy=POLICY,
        configuration_hash=scenario.configurations["plan"].configuration_hash,
    )
    quarantined = local.model_copy(
        update={"status": VerificationState.QUARANTINED}, deep=True
    )

    event = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=None,
        local=quarantined,
        attempted_configuration_hashes=[],
    )

    stored = await scenario.store.get_failure_event(event.contract_id)
    assert stored is not None
    assert stored.assigned_owner is FailureOwner.SAFETY
    assert stored.retryable is False
    assert stored.failure_type is FailureType.POLICY_RISK


async def test_a_classified_failure_carries_the_attempted_configuration_hashes() -> None:
    """§9.7's last rule is only enforceable because the event accumulates them."""

    scenario = await setup_pipeline()
    hashes = [
        scenario.configurations["plan"].configuration_hash,
        scenario.configurations["verify"].configuration_hash,
    ]

    event = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=hashes,
    )

    stored = await scenario.store.get_failure_event(event.contract_id)
    assert stored is not None
    assert stored.attempted_configuration_hashes == hashes


async def test_a_failure_with_no_error_and_no_verdict_is_unknown_and_stops_recovery() -> None:
    """An unclassified failure is not a transient one; registry §5.4 makes it a hard stop."""

    scenario = await setup_pipeline()

    event = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=None,
        local=None,
        attempted_configuration_hashes=[],
    )

    assert event.assigned_owner is FailureOwner.UNKNOWN
    assert event.retryable is False


# -------------------------------------------------------------- recovery_decision


async def test_a_configuration_failure_reselects_outside_the_attempted_set() -> None:
    """§9.7: an equivalent failed configuration does not come back without new evidence.

    The attempted hash is offered again among the candidates and must not be chosen. Asserting
    only "a hash was returned" would pass on a guard that handed back the one that just failed.
    """

    scenario = await setup_pipeline()
    attempted = scenario.configurations["plan"].configuration_hash
    untried = [digest(f"candidate-{index}") for index in range(6)]
    failure = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=[attempted],
    )

    decision = await scenario.pipeline.recovery_decision(
        failure=failure,
        budget=ResourceBudget(
            maximum_cost=Decimal("10"),
            maximum_latency_ms=600_000,
            maximum_attempts=5,
            maximum_tool_calls=100,
        ),
        candidate_hashes=[attempted, *untried],
    )

    assert decision.guard.action == "RESELECT"
    assert decision.authority_scope == "ROUTER_RESELECT"
    assert decision.action.action_code == "ROUTER_RESELECT"
    assert decision.next_configuration_hash is not None
    assert decision.next_configuration_hash != attempted
    assert decision.next_configuration_hash in untried


async def test_a_second_distinct_configuration_failure_re_types_to_structural() -> None:
    """Two configurations failing on one node is evidence about the node, not the configurations.

    The only path from router authority to planner authority, taken on a stated condition, and
    the reason code is asserted so that a replan reached by some other route would not pass.
    """

    scenario = await setup_pipeline()
    failure = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=[digest("first"), digest("second")],
    )

    decision = await scenario.pipeline.recovery_decision(
        failure=failure,
        budget=ResourceBudget(
            maximum_cost=Decimal("10"),
            maximum_latency_ms=600_000,
            maximum_attempts=5,
            maximum_tool_calls=100,
        ),
        candidate_hashes=[digest(f"candidate-{index}") for index in range(6)],
    )

    assert decision.guard.owner is FailureOwner.STRUCTURAL
    assert decision.guard.reason_code == "CONFIGURATION_SPACE_EXHAUSTED"
    assert decision.authority_scope == "PLANNER_REPLAN"
    assert decision.action.retry_allowed is False
    assert decision.next_configuration_hash is None


async def test_a_stopped_recovery_names_no_next_configuration() -> None:
    """A decision that named one could drive the retry the stop exists to prevent."""

    scenario = await setup_pipeline()
    failure = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=None,
        local=None,
        attempted_configuration_hashes=[],
    )

    decision = await scenario.pipeline.recovery_decision(
        failure=failure,
        budget=ResourceBudget(
            maximum_cost=Decimal("10"),
            maximum_latency_ms=600_000,
            maximum_attempts=5,
            maximum_tool_calls=100,
        ),
        candidate_hashes=[digest("candidate")],
    )

    assert decision.guard.action == "STOP"
    assert decision.next_configuration_hash is None
    assert decision.action.retry_allowed is False


async def test_experience_recorded_after_a_failure_counts_as_new_evidence_for_its_hash() -> None:
    """§9.7's escape hatch, and the reason the comparison is strictly *after* the failure.

    The same call is made twice against the same candidate list, differing only in whether an
    experience record for the attempted hash was sealed after the failure. Without the record the
    hash stays blocked; with it, it is eligible again.

    The node's local verdict is recorded first, and that is not scene-setting: ``prior_success``
    is the verified success rate over in-domain experience, so a projection carrying no verdict
    would drive ``EVI = untried_fraction × prior_success`` to zero and stop recovery for a reason
    that has nothing to do with the evidence rule under test.
    """

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await scenario.pipeline.record_local(
        run=scenario.run,
        task=scenario.task,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        session_id=PRODUCER_SESSION,
        results=[
            v01_result(
                "git-diff",
                VerificationStatus.PASS,
                scenario.run.run_id,
                evidence=(f"git-diff-sha256:{digest('plan')}",),
            )
        ],
        policy=POLICY,
        configuration_hash=scenario.configurations["plan"].configuration_hash,
    )
    attempted = scenario.configurations["plan"].configuration_hash
    failure = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=[attempted],
    )
    budget = ResourceBudget(
        maximum_cost=Decimal("10"),
        maximum_latency_ms=600_000,
        maximum_attempts=5,
        maximum_tool_calls=100,
    )

    blocked = await scenario.pipeline.recovery_decision(
        failure=failure, budget=budget, candidate_hashes=[attempted]
    )

    later = DefaultFeedbackPipeline(
        scenario.store,
        scenario.materializer,
        lambda: SEALED_AT + timedelta(hours=1),
        OPERATOR,
    )
    await later.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )
    unblocked = await later.recovery_decision(
        failure=failure, budget=budget, candidate_hashes=[attempted]
    )

    assert blocked.guard.reason_code == "EVI_BELOW_THRESHOLD"
    assert blocked.next_configuration_hash is None
    assert unblocked.guard.action == "RESELECT"
    assert unblocked.next_configuration_hash == attempted


async def test_the_budgets_last_permitted_attempt_failing_stops_recovery() -> None:
    """``>=`` and not ``>``: with ``>`` a budget of three attempts would buy four."""

    scenario = await setup_pipeline()
    failure = await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["plan"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=[digest("first")],
    )

    decision = await scenario.pipeline.recovery_decision(
        failure=failure,
        budget=ResourceBudget(
            maximum_cost=Decimal("10"),
            maximum_latency_ms=600_000,
            maximum_attempts=1,
            maximum_tool_calls=100,
        ),
        candidate_hashes=[digest(f"candidate-{index}") for index in range(6)],
    )

    assert decision.guard.reason_code == "ATTEMPT_CAP_REACHED"
    assert decision.next_configuration_hash is None


# ------------------------------------------------------------------ full lifecycle


async def test_the_four_methods_run_in_order_and_leave_four_kinds_of_row() -> None:
    """The lifecycle end to end, with the store as the only witness.

    Nothing here asserts on a returned object: every count and every status is read back, because
    "the pipeline returned a record" and "the record is durable" are different claims and only
    the second one matters to anything downstream.
    """

    scenario = await setup_pipeline()
    await route(scenario, "plan")
    await route(scenario, "verify")
    for key in ("plan", "verify"):
        await scenario.pipeline.record_local(
            run=scenario.run,
            task=scenario.task,
            execution_instance_id=scenario.nodes[key].execution_instance_id,
            session_id=PRODUCER_SESSION,
            results=[
                v01_result(
                    "git-diff",
                    VerificationStatus.PASS,
                    scenario.run.run_id,
                    evidence=(f"git-diff-sha256:{digest(key)}",),
                )
            ],
            policy=POLICY,
            configuration_hash=scenario.configurations[key].configuration_hash,
        )
    await scenario.pipeline.classify_failure(
        run=scenario.run,
        execution_instance_id=scenario.nodes["verify"].execution_instance_id,
        error=ErrorSummary(code="RUNTIME_VERSION_DRIFT", message="runtime moved"),
        local=None,
        attempted_configuration_hashes=[
            scenario.configurations["verify"].configuration_hash
        ],
    )
    await scenario.pipeline.record_final(
        run=scenario.run,
        status=VerificationState.PASS,
        source=ExperienceSourceKind.RUN,
        principal=PRINCIPAL,
    )

    assert len(await scenario.store.list_verification_results(workspace_id=WORKSPACE_ID)) == 2
    assert len(await scenario.store.list_failure_events(workspace_id=WORKSPACE_ID)) == 1
    projections = await scenario.store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert len(projections) == 2
    assert all(record.eligible_for_learning for record in projections)
    assert all(
        record.contradiction_status is ContradictionStatus.NONE for record in projections
    )
    types = {
        event.normalized_type for event in await scenario.store.list_events(scenario.run.run_id)
    }
    assert types == {
        EventType.VERIFICATION_RESULT_RECORDED,
        EventType.EXPERIENCE_CREATED,
    }
