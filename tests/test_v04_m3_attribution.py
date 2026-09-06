"""Attribution: dividing one run's outcome between its nodes, and never rewriting the answer.

``dep-heuristic+retry-delta/1`` is a heuristic and the tests here are about the two things that
must be true of *any* method that fills SDD §7.10's ``attribution``, however good it later gets:

**It is deterministic and order-independent.** Shares are a division by a sum, and floating-point
addition is not associative, so a method that summed in the caller's order would give one node
two different scores depending on which list it arrived in. Two of the tests below pass the same
nodes in two orders and require identical floats — not approximately identical, identical, because
the score goes into a derived id and into a content hash.

**It never edits.** §9.6 makes attribution a derived, versioned view, and registry §17 forbids
rewriting a historical row. ``AC4-M3-026`` is asserted with a store spy that counts writes naming
a contract id the table already holds, and requires zero — an assertion the final table cannot
make, because an append and a rewrite-then-append leave exactly the same rows behind.

Beside those, the arithmetic itself is pinned: a node with more dependencies is credited with
less, a retry that turned a failure into a pass is credited for the *change* rather than for the
state, and every source of ignorance halves a confidence that starts at one half.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import (
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
    TaskType,
    TemplateStatus,
    WorkflowNodeSpec,
    WorkflowTemplate,
)
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    DecisionType,
    ExecutionConfiguration,
    ExperienceRecord,
    IndependentVerificationResult,
    NodeContract,
    RoutingDecisionReceipt,
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
from accretion.feedback.attribution import (
    METHOD_VERSION,
    AttributionInput,
    DependencyAttributor,
)
from accretion.feedback.experience import (
    EXPERIENCE_ID_LABEL,
    UNATTRIBUTED,
    ExperienceProjector,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.identity import execution_instance_id
from accretion.templates import compute_template_checksum

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
FIXTURE: dict[str, Any] = json.loads(
    (FIXTURE_ROOT / "node_contract" / "minimal.json").read_text(encoding="utf-8")
)
WORKSPACE_ID: str = FIXTURE["workspace_id"]
PROJECT_ID: str = FIXTURE["project_id"]
RUN_GRAPH_ID: str = FIXTURE["run_graph_id"]

SEALED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M3 attribution test",
    status=PrincipalStatus.ACTIVE,
)
OPERATOR = PrincipalRef(
    principal_id="usr_OPERATOR000000000000000000",
    display_name="v0.4 M3 deployment operator",
    status=PrincipalStatus.ACTIVE,
)


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


class CountingStore(MemoryStore):
    """The ``AC4-M3-026`` spy: every experience-record write, and which of them were rewrites."""

    def __init__(self) -> None:
        super().__init__()
        self.record_puts: list[str] = []
        self.rewrites: list[str] = []

    async def put_experience_record(
        self, record: ExperienceRecord, *, experience_id: str | None = None
    ) -> ExperienceRecord:
        if record.contract_id in self.v04_contracts["experience_records"]:
            self.rewrites.append(record.contract_id)
        self.record_puts.append(record.contract_id)
        return await super().put_experience_record(record, experience_id=experience_id)


class FakeMaterializer:
    """``materialize`` with a call counter and a failure switch, and no P7 service behind it."""

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


def entry(
    node_key: str,
    *,
    attempt: int = 1,
    status: VerificationState = VerificationState.PASS,
    final: VerificationState | None = VerificationState.PASS,
    node_id: str | None = None,
    run_id: str = "run_attribution",
) -> AttributionInput:
    """One node's evidence, with the real execution-instance derivation behind it."""

    return AttributionInput(
        execution_instance_id=execution_instance_id(run_id, node_key, attempt),
        node_id=node_id or node_key,
        node_key=node_key,
        attempt=attempt,
        local_status=status,
        final_status=final,
    )


def graph_of(*edges: tuple[str, str], nodes: tuple[str, ...]) -> RunGraph:
    return RunGraph(
        run_graph_id=RUN_GRAPH_ID,
        run_id="run_attribution",
        task_id="tsk_attribution",
        template_record_id="tpl_record_attribution",
        template_id="attribution",
        template_version="1.0.0",
        template_checksum="attribution-checksum",
        nodes=[
            RunNode(node_id=name, key=name, kind=GraphNodeKind.TASK, label=name)
            for name in nodes
        ],
        edges=[
            RunEdge(
                edge_id=f"{source}-{target}",
                key=f"{source}-{target}",
                source=source,
                target=target,
                kind=GraphEdgeKind.NORMAL,
            )
            for source, target in edges
        ],
    )


# ------------------------------------------------------------------ pure method


def test_a_node_with_more_dependencies_is_credited_with_less_of_the_run() -> None:
    """More arrived already done, so less of the outcome is this node's doing.

    ``verify`` has two parents and the other two have none, so the weights are 1/3, 1 and 1 and
    the shares are those normalised: 1/7 against 3/7. Asserted as an inequality *and* as the
    exact values, because the inequality alone would still hold if the weights were reversed by
    accident and the whole method inverted.
    """

    attributor = DependencyAttributor(MemoryStore())
    graph = graph_of(
        ("plan", "verify"), ("build", "verify"), nodes=("plan", "build", "verify")
    )
    entries = [entry("plan"), entry("build"), entry("verify")]

    summaries = attributor.compute(entries, graph=graph)

    scores = {item.node_key: summaries[item.execution_instance_id].score for item in entries}
    assert scores["verify"] is not None and scores["plan"] is not None
    assert scores["verify"] < scores["plan"]
    assert scores["plan"] == scores["build"] == round(3 / 7, 6)
    assert scores["verify"] == round(1 / 7, 6)


def test_with_every_node_passing_the_shares_of_one_run_sum_to_one() -> None:
    """A run is credited once. Shares that summed to more would credit it twice.

    The tolerance is the recorded precision and nothing looser: the scores are rounded to six
    decimals before they are stored — they go inside a derived id and a content hash, so an
    unrounded float would make two runs of one computation two rows — and three sixths do not
    re-add to exactly one at that precision. A tolerance any wider than that would stop catching
    a share that was genuinely mis-normalised.
    """

    attributor = DependencyAttributor(MemoryStore())
    graph = graph_of(("a", "b"), ("b", "c"), nodes=("a", "b", "c"))
    entries = [entry("a"), entry("b"), entry("c")]

    summaries = attributor.compute(entries, graph=graph)

    total = sum(
        summary.score for summary in summaries.values() if summary.score is not None
    )
    assert abs(total - 1.0) <= len(entries) * 5e-7


def test_with_no_graph_every_node_gets_an_equal_share() -> None:
    """A pruned graph loses the dependencies, not the evidence."""

    attributor = DependencyAttributor(MemoryStore())
    entries = [entry("a"), entry("b"), entry("c"), entry("d")]

    summaries = attributor.compute(entries, graph=None)

    assert {summary.score for summary in summaries.values()} == {0.25}


def test_two_edges_between_one_pair_count_as_one_dependency() -> None:
    """A retry edge beside a normal edge is not a second thing the node depended on."""

    attributor = DependencyAttributor(MemoryStore())
    single = graph_of(("a", "b"), nodes=("a", "b"))
    doubled = RunGraph.model_validate(
        {
            **single.model_dump(mode="python"),
            "edges": [
                *single.model_dump(mode="python")["edges"],
                {
                    "edge_id": "a-b-retry",
                    "key": "a-b-retry",
                    "source": "a",
                    "target": "b",
                    "kind": GraphEdgeKind.RETRY.value,
                },
            ],
        }
    )
    entries = [entry("a"), entry("b")]

    assert attributor.compute(entries, graph=single) == attributor.compute(
        entries, graph=doubled
    )


def test_a_retry_that_turned_a_failure_into_a_pass_is_credited_for_the_change() -> None:
    """The reselection is what the second attempt did; the delta is the size of it.

    Attempt one failed and attempt two passed, so the delta is ``(1 − −1) / 2 = 1`` and the
    second attempt's score is twice its bare share. The first attempt keeps its negative share,
    because it did fail and the retry did not undo that.
    """

    attributor = DependencyAttributor(MemoryStore())
    first = entry(
        "implement",
        attempt=1,
        status=VerificationState.FAIL,
        final=VerificationState.PASS,
    )
    second = entry("implement", attempt=2, status=VerificationState.PASS)

    summaries = attributor.compute([first, second], graph=None)

    assert summaries[first.execution_instance_id].score == -0.5
    assert summaries[second.execution_instance_id].score == 1.0


def test_a_third_attempt_is_measured_against_the_second_and_not_against_the_first() -> None:
    """Otherwise one retry is credited with every retry's worth of change."""

    attributor = DependencyAttributor(MemoryStore())
    entries = [
        entry("implement", attempt=1, status=VerificationState.FAIL),
        entry("implement", attempt=2, status=VerificationState.FAIL),
        entry("implement", attempt=3, status=VerificationState.PASS),
    ]

    summaries = attributor.compute(entries, graph=None)

    third = summaries[entries[2].execution_instance_id].score
    second = summaries[entries[1].execution_instance_id].score
    # Third: share 1/3, direction +1, delta (+1 − −1)/2 = 1 → 2/3. Second: no change from the
    # first, so delta 0 and the bare negative share.
    assert third is not None and round(third, 6) == round(2 / 3, 6)
    assert second is not None and round(second, 6) == round(-1 / 3, 6)


def test_an_undecided_verdict_attributes_nothing_and_halves_the_confidence() -> None:
    """INCONCLUSIVE points nowhere. A zero score with full confidence would claim it did."""

    attributor = DependencyAttributor(MemoryStore())
    entries = [entry("a", status=VerificationState.INCONCLUSIVE), entry("b")]

    summaries = attributor.compute(entries, graph=None)

    undecided = summaries[entries[0].execution_instance_id]
    decided = summaries[entries[1].execution_instance_id]
    assert undecided.score == 0.0
    assert undecided.confidence == 0.25
    assert decided.confidence == 0.5


def test_a_run_that_has_not_been_judged_yet_halves_the_confidence_again() -> None:
    """ADR-048: a node's outcome is weaker evidence while the run it belongs to is ungraded."""

    attributor = DependencyAttributor(MemoryStore())
    entries = [entry("a", final=None)]

    summaries = attributor.compute(entries, graph=None)

    assert summaries[entries[0].execution_instance_id].confidence == 0.25


def test_the_same_nodes_in_a_different_order_produce_byte_identical_scores() -> None:
    """The score is inside a derived id and a content hash, so "close enough" is not enough."""

    attributor = DependencyAttributor(MemoryStore())
    graph = graph_of(("a", "c"), ("b", "c"), nodes=("a", "b", "c"))
    forward = [entry("a"), entry("b"), entry("c")]
    backward = [entry("c"), entry("b"), entry("a")]

    assert attributor.compute(forward, graph=graph) == attributor.compute(
        backward, graph=graph
    )


def test_every_summary_names_the_method_that_produced_it() -> None:
    """§7.10 makes ``method_version`` mandatory even when the score is null, and this is why."""

    attributor = DependencyAttributor(MemoryStore())

    summaries = attributor.compute([entry("a")], graph=None)

    assert {summary.method_version for summary in summaries.values()} == {METHOD_VERSION}
    assert METHOD_VERSION != UNATTRIBUTED.method_version


def test_no_entries_attribute_nothing_rather_than_dividing_by_zero() -> None:
    """A run with no routed node is not an error; it is a run with nothing to attribute."""

    assert DependencyAttributor(MemoryStore()).compute([], graph=None) == {}


# ------------------------------------------------------------------ re-attribution


async def setup_two_projected_nodes() -> tuple[
    CountingStore,
    ExperienceProjector,
    DependencyAttributor,
    Run,
    Experience,
    list[ExperienceRecord],
]:
    """Two routed nodes of one run, projected unattributed, with a real graph behind them.

    Projected through the real projector rather than hand-built, because ``reattribute`` reads
    ``node_id``, ``node_key``, ``attempt`` and ``experience_id`` out of the labels the projector
    writes, and a hand-built record would let this file pass with those labels missing.
    """

    store = CountingStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 M3 attribution",
            repository_path=Path("/tmp/accretion-v04-m3-attribution"),
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
    await store.create_run(run)
    template = WorkflowTemplate(
        template_record_id="wft_01K4DQ9HVJXBQBN3YF83E5Y9TF",
        template_id="m3-attribution",
        version="1.0.0",
        mode=ExecutionMode.GRAPH,
        nodes=[
            WorkflowNodeSpec(key="plan", kind=GraphNodeKind.TASK, label="Plan"),
            WorkflowNodeSpec(key="verify", kind=GraphNodeKind.TASK, label="Verify"),
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
            task_id=run.task_id,
            template_record_id=template.template_record_id,
            template_id=template.template_id,
            template_version=template.version,
            template_checksum=template.checksum,
            nodes=[
                RunNode(node_id="plan", key="plan", kind=GraphNodeKind.TASK, label="plan"),
                RunNode(
                    node_id="verify", key="verify", kind=GraphNodeKind.TASK, label="verify"
                ),
            ],
            edges=[
                RunEdge(
                    edge_id="plan-verify",
                    key="plan-verify",
                    source="plan",
                    target="verify",
                    kind=GraphEdgeKind.NORMAL,
                )
            ],
        )
    )

    experience = Experience(
        experience_id=new_id("experience"),
        project_id=PROJECT_ID,
        repository_identity=digest(PROJECT_ID),
        task_id=run.task_id,
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
        content={"nodes": ["plan", "verify"]},
        content_digest=digest("segment"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience.experience_id,
        input_digest=digest("embedding"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)

    projector = ExperienceProjector(
        store,
        FakeMaterializer(
            ExperienceDetail(
                experience=experience,
                segments=[segment],
                embedding_version="v1",
                embedding_input_digest=digest("embedding"),
            )
        ),
        lambda: SEALED_AT,
        OPERATOR,
    )

    records: list[ExperienceRecord] = []
    for index, node_key in enumerate(("plan", "verify")):
        node = build(
            NodeContract,
            contract_id=new_id("node_contract"),
            node_id=node_key,
            objective=f"do the {node_key} work",
            execution_instance_id=execution_instance_id(run.run_id, node_key, 1),
            labels={"run_id": run.run_id, "node_key": node_key, "attempt": "1"},
        )
        configuration = make_configuration(node_key)
        receipt = build(
            RoutingDecisionReceipt,
            contract_id=new_id("routing_receipt"),
            routing_request_id=new_id("routing_request"),
            node_contract_hash=node.immutable_hash,
            decision_type=DecisionType.EXPLOIT.value,
            selected_configuration_id=configuration.contract_id,
            selected_configuration_hash=configuration.configuration_hash,
        )
        local = build(
            IndependentVerificationResult,
            contract_id=new_id("independent_verification_result"),
            execution_instance_id=node.execution_instance_id,
            status=VerificationState.PASS.value,
        )
        records.append(
            await projector.project(
                run=run,
                node=node,
                receipt=receipt,
                configuration=configuration,
                local=local,
                final_status=VerificationState.PASS,
                principal=PRINCIPAL,
            )
        )
        assert index < 2
    return store, projector, DependencyAttributor(store), run, experience, records


def make_configuration(marker: str) -> ExecutionConfiguration:
    document: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "execution_configuration" / "minimal.json").read_text(
            encoding="utf-8"
        )
    )
    document["environment"]["environment"]["policy_profile"] = f"profile-{marker}"
    document["contract_id"] = new_id("execution_configuration")
    document.pop("content_hash", None)
    document.pop("configuration_hash", None)
    return ExecutionConfiguration.model_validate(document)


@pytest.mark.acceptance("AC4-M3-026")
async def test_reattribution_appends_a_revision_and_leaves_the_root_bytes_untouched() -> None:
    """AC4-M3-026. A recomputed attribution is a new row under the same experience.

    Three assertions, and all three are needed. The revision exists and carries the new score;
    the root record read back out of the store is byte-identical to what it was before; and the
    spy counted **zero** writes naming a contract id the table already held, which is the only
    way to tell an append from a rewrite followed by an append.
    """

    store, projector, attributor, run, experience, records = (
        await setup_two_projected_nodes()
    )
    before = {
        record.contract_id: await store.get_experience_record(record.contract_id)
        for record in records
    }
    assert all(record.attribution == UNATTRIBUTED for record in records)

    revisions = await attributor.reattribute(
        run=run, records=records, projector=projector, principal=PRINCIPAL
    )

    assert len(revisions) == 2
    assert store.rewrites == []
    for record in records:
        assert await store.get_experience_record(record.contract_id) == before[
            record.contract_id
        ]
    chain = await store.list_experience_record_revisions(
        experience.experience_id, workspace_id=WORKSPACE_ID
    )
    assert len(chain) == 4
    for revision in revisions:
        assert revision.attribution.method_version == METHOD_VERSION
        assert revision.supersedes_contract_id in before
        assert revision.labels[EXPERIENCE_ID_LABEL] == experience.experience_id


async def test_a_dependent_node_is_attributed_less_than_the_node_it_depended_on() -> None:
    """The graph read from the store, not a graph the test handed in."""

    store, projector, attributor, run, experience, records = (
        await setup_two_projected_nodes()
    )

    revisions = await attributor.reattribute(
        run=run, records=records, projector=projector, principal=PRINCIPAL
    )

    by_node = {
        revision.labels["node_key"]: revision.attribution.score for revision in revisions
    }
    assert by_node["verify"] is not None and by_node["plan"] is not None
    assert by_node["verify"] < by_node["plan"]


async def test_a_reattribution_that_changes_nothing_writes_nothing() -> None:
    """Silence is the right answer to a re-run that found the same numbers.

    Appending an identical-but-for-the-clock revision on every pass would grow the chain without
    bound and make "has the attribution moved?" unanswerable from the history.
    """

    store, projector, attributor, run, _, records = await setup_two_projected_nodes()
    revisions = await attributor.reattribute(
        run=run, records=records, projector=projector, principal=PRINCIPAL
    )
    writes = len(store.record_puts)

    again = await attributor.reattribute(
        run=run, records=revisions, projector=projector, principal=PRINCIPAL
    )

    assert again == []
    assert len(store.record_puts) == writes


async def test_a_record_with_no_experience_label_is_skipped_rather_than_guessed_at() -> None:
    """Filing a revision under the wrong parent is the quietest corruption this table allows."""

    store, projector, attributor, run, _, records = await setup_two_projected_nodes()
    orphan = build(
        ExperienceRecord,
        contract_id=new_id("experience"),
        labels={},
        source_node_execution_id=execution_instance_id(run.run_id, "orphan", 1),
    )

    written = await attributor.reattribute(
        run=run, records=[orphan], projector=projector, principal=PRINCIPAL
    )

    assert written == []
    assert store.record_puts == [record.contract_id for record in records]
