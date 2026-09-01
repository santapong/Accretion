"""Inherited v0.2 P5 planning proofs (V02-P5-003, -005, -009).

These tests tamper with real ``FragmentWorkflowPlanner`` output rather than
hand-built graphs, so the validator is exercised against the topology the
product actually plans. Every rejection asserts the exact finding code, and the
capability tests build the snapshot so the tampered capability is *known* — the
``UNKNOWN_CAPABILITY`` short-circuit must not be able to carry the assertion.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    EventType,
    ExpectedHorizon,
    GraphEdgeKind,
    GraphNodeStatus,
    Project,
    Provider,
    RiskLevel,
    RunState,
    Task,
    TaskBudgets,
    TaskEnvelope,
    TaskProfile,
    TaskType,
)
from accretion.ids import new_id
from accretion.orchestration.fragments import FragmentWorkflowPlanner
from accretion.orchestration.models import (
    CapabilitySnapshot,
    DynamicWorkflowEdgeSpec,
    GraphValidationResult,
    GraphValidationStatus,
    PolicySnapshot,
    ReplanReason,
    WorkflowProposal,
    WorkflowValidationOutcome,
)
from accretion.orchestration.service import (
    DynamicWorkflowConflictError,
    DynamicWorkflowService,
)
from accretion.orchestration.validator import (
    MAX_FANOUT,
    MAX_TRAVERSALS,
    GraphValidator,
)
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager

KNOWN_CAPABILITY = "repo.read"


def build_task(
    tmp_path: Path,
    *,
    risk: RiskLevel = RiskLevel.LOW,
    feedback: float = 0.2,
    parallel_runs: int = 1,
    capabilities: list[str] | None = None,
) -> tuple[Task, TaskProfile]:
    project = Project(
        project_id=new_id("project"),
        name="M8 inherited planning",
        repository_path=tmp_path,
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Exercise inherited P5 validation proofs.",
            task_type=TaskType.REVIEW,
            risk_level=risk,
            allowed_capabilities=list(capabilities or []),
            budgets=TaskBudgets(max_parallel_runs=parallel_runs),
        ),
        prompt_contract_id=new_id("prompt"),
    )
    profile = TaskProfile(
        profile_id=new_id("profile"),
        task_id=task.envelope.task_id,
        complexity=0.5,
        structure_certainty=0.5,
        feedback_dependency=feedback,
        dependency_complexity=0.5,
        parallelism_potential=0.2,
        uncertainty=0.5,
        verifier_strength=0.8,
        risk=risk,
        irreversible_actions=False,
        expected_horizon=ExpectedHorizon.MEDIUM,
        profile_confidence=0.9,
        semantic_rationale="M8 inherited fixture",
    )
    return task, profile


def build_snapshots(
    *,
    denied: set[str] | None = None,
    allowed: set[str] | None = None,
    maximum_risk: RiskLevel = RiskLevel.CRITICAL,
) -> tuple[CapabilitySnapshot, PolicySnapshot]:
    """A snapshot in which ``KNOWN_CAPABILITY`` is always a *known* capability."""

    capabilities = CapabilitySnapshot(
        capabilities={KNOWN_CAPABILITY: RiskLevel.LOW, "repo.write": RiskLevel.MEDIUM},
        verifiers={"output-contract", "git-diff", "trajectory-policy"},
        available_runtimes={
            Provider.CLAUDE,
            Provider.CODEX,
            Provider.DETERMINISTIC,
            Provider.FAKE,
        },
    )
    policy = PolicySnapshot(
        denied_capabilities=set(denied or set()),
        allowed_capabilities=set(allowed or set()),
        required_verifiers={"output-contract", "trajectory-policy"},
        maximum_risk=maximum_risk,
    )
    return capabilities, policy


def codes(result: GraphValidationResult) -> set[str]:
    return {finding.code for finding in result.errors}


def node_scoped_codes(result: GraphValidationResult) -> set[str]:
    return {
        finding.code
        for finding in result.errors
        if finding.path is not None and finding.path.startswith("nodes.")
    }


# --------------------------------------------------------------------------
# V02-P5-003 — traversal, cycle and fan-out bounds
# --------------------------------------------------------------------------


@pytest.mark.acceptance("V02-P5-003")
def test_unbounded_loop_back_cycle_in_planned_graph_is_rejected(tmp_path: Path) -> None:
    task, profile = build_task(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    tampered = proposal.model_copy(
        update={
            "edges": [
                *proposal.edges,
                DynamicWorkflowEdgeSpec(
                    local_id="m8-unbounded-return",
                    source="verify",
                    target="act",
                    kind=GraphEdgeKind.NORMAL,
                ),
            ]
        }
    )
    capabilities, policy = build_snapshots()

    result = GraphValidator().validate(tampered, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "UNBOUNDED_CYCLE" in codes(result)
    assert result.normalized_graph_hash is None


@pytest.mark.acceptance("V02-P5-003")
@pytest.mark.parametrize("bound", [None, MAX_TRAVERSALS + 1])
def test_retry_edge_outside_the_traversal_ceiling_is_rejected(
    tmp_path: Path, bound: int | None
) -> None:
    """The validator must bound RETRY traversals independently of the contract.

    ``DynamicWorkflowEdgeSpec`` already refuses an absent or over-ceiling bound,
    so the tampering here bypasses that layer with ``model_copy`` — exactly the
    shape of a graph that reached the validator without contract validation.
    The contract layer is asserted separately below so both doors stay shut.
    """

    task, profile = build_task(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    retry = DynamicWorkflowEdgeSpec(
        local_id="m8-retry-over-ceiling",
        source="verify",
        target="act",
        kind=GraphEdgeKind.RETRY,
        max_traversals=MAX_TRAVERSALS,
    ).model_copy(update={"max_traversals": bound})
    assert retry.max_traversals == bound
    tampered = proposal.model_copy(update={"edges": [*proposal.edges, retry]})
    capabilities, policy = build_snapshots()

    result = GraphValidator().validate(tampered, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "UNBOUNDED_TRAVERSAL" in codes(result)


@pytest.mark.acceptance("V02-P5-003")
@pytest.mark.parametrize("bound", [None, MAX_TRAVERSALS + 1])
def test_retry_edge_outside_the_traversal_ceiling_fails_contract_validation(
    bound: int | None,
) -> None:
    with pytest.raises(ValidationError):
        DynamicWorkflowEdgeSpec(
            local_id="m8-retry-over-ceiling",
            source="verify",
            target="act",
            kind=GraphEdgeKind.RETRY,
            max_traversals=bound,
        )


@pytest.mark.acceptance("V02-P5-003")
def test_fan_out_above_the_constant_ceiling_is_rejected(tmp_path: Path) -> None:
    """Budget headroom is generous, so the constant ceiling alone must bind.

    The literals below pin the reviewed ceiling; loosening ``MAX_FANOUT`` in the
    validator must fail here rather than silently widen the accepted fan-out.
    """

    assert MAX_FANOUT == 4
    assert MAX_TRAVERSALS == 3
    task, profile = build_task(tmp_path, parallel_runs=8)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    tampered = proposal.model_copy(
        update={
            "edges": [
                *proposal.edges,
                *(
                    DynamicWorkflowEdgeSpec(
                        local_id=f"m8-fanout-{index}",
                        source="start",
                        target="complete",
                        kind=GraphEdgeKind.FANOUT,
                    )
                    for index in range(5)
                ),
            ]
        }
    )
    capabilities, policy = build_snapshots()

    result = GraphValidator().validate(tampered, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "FANOUT_EXCEEDED" in codes(result)


@pytest.mark.acceptance("V02-P5-003")
def test_fan_out_above_the_task_concurrency_budget_is_rejected(tmp_path: Path) -> None:
    """Fan-out below ``MAX_FANOUT`` still rejects when the budget is smaller."""

    concurrency = 2
    assert concurrency < MAX_FANOUT
    task, profile = build_task(tmp_path, parallel_runs=concurrency)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    fanout = concurrency + 1
    assert fanout <= MAX_FANOUT
    tampered = proposal.model_copy(
        update={
            "edges": [
                *proposal.edges,
                *(
                    DynamicWorkflowEdgeSpec(
                        local_id=f"m8-budget-fanout-{index}",
                        source="start",
                        target="complete",
                        kind=GraphEdgeKind.FANOUT,
                    )
                    for index in range(fanout)
                ),
            ]
        }
    )
    capabilities, policy = build_snapshots()

    result = GraphValidator().validate(tampered, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "FANOUT_EXCEEDED" in codes(result)


@pytest.mark.acceptance("V02-P5-003")
def test_retry_edge_at_the_traversal_ceiling_is_accepted(tmp_path: Path) -> None:
    """The same tampering shape, bounded inside the ceiling, must be ACCEPTED."""

    task, profile = build_task(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    bounded = proposal.model_copy(
        update={
            "edges": [
                *proposal.edges,
                DynamicWorkflowEdgeSpec(
                    local_id="m8-retry-at-ceiling",
                    source="verify",
                    target="act",
                    kind=GraphEdgeKind.RETRY,
                    max_traversals=MAX_TRAVERSALS,
                ),
            ]
        }
    )
    capabilities, policy = build_snapshots()

    result = GraphValidator().validate(bounded, capabilities, policy, task.envelope.budgets)

    assert result.errors == []
    assert result.status is GraphValidationStatus.ACCEPT
    assert result.normalized_graph_hash is not None


# --------------------------------------------------------------------------
# V02-P5-005 — privilege and risk ceilings on a *known* capability
# --------------------------------------------------------------------------


def planned_proposal_using_the_known_capability(
    tmp_path: Path, **kwargs: object
) -> tuple[Task, WorkflowProposal]:
    task, profile = build_task(
        tmp_path,
        capabilities=[KNOWN_CAPABILITY],
        **kwargs,  # type: ignore[arg-type]
    )
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    assert proposal.required_capabilities == [KNOWN_CAPABILITY]
    assert any(KNOWN_CAPABILITY in node.capability_refs for node in proposal.nodes), (
        "the planner must attach the capability to an agent node"
    )
    return task, proposal


@pytest.mark.acceptance("V02-P5-005")
def test_denied_capability_rejects_without_the_unknown_capability_shortcut(
    tmp_path: Path,
) -> None:
    task, proposal = planned_proposal_using_the_known_capability(tmp_path)
    capabilities, policy = build_snapshots(denied={KNOWN_CAPABILITY}, allowed={"repo.write"})
    assert KNOWN_CAPABILITY in capabilities.capabilities

    result = GraphValidator().validate(proposal, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "UNKNOWN_CAPABILITY" not in codes(result)
    # The denial must win over the (also violated) allow-list ceiling.
    assert "DENIED_CAPABILITY" in node_scoped_codes(result)
    assert "PRIVILEGE_EXPANSION" not in codes(result)


@pytest.mark.acceptance("V02-P5-005")
def test_capability_outside_a_non_empty_allow_list_is_privilege_expansion(
    tmp_path: Path,
) -> None:
    task, proposal = planned_proposal_using_the_known_capability(tmp_path)
    capabilities, policy = build_snapshots(allowed={"repo.write"})
    assert KNOWN_CAPABILITY in capabilities.capabilities
    assert KNOWN_CAPABILITY not in policy.allowed_capabilities

    result = GraphValidator().validate(proposal, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "UNKNOWN_CAPABILITY" not in codes(result)
    assert "DENIED_CAPABILITY" not in codes(result)
    assert "PRIVILEGE_EXPANSION" in node_scoped_codes(result)


@pytest.mark.acceptance("V02-P5-005")
def test_node_risk_above_the_policy_ceiling_is_risk_expansion(tmp_path: Path) -> None:
    task, proposal = planned_proposal_using_the_known_capability(tmp_path, risk=RiskLevel.HIGH)
    capabilities, policy = build_snapshots(allowed={KNOWN_CAPABILITY}, maximum_risk=RiskLevel.LOW)
    assert any(node.risk_level is RiskLevel.HIGH for node in proposal.nodes)

    result = GraphValidator().validate(proposal, capabilities, policy, task.envelope.budgets)

    assert result.status is GraphValidationStatus.REJECT
    assert "UNKNOWN_CAPABILITY" not in codes(result)
    assert "DENIED_CAPABILITY" not in codes(result)
    assert "PRIVILEGE_EXPANSION" not in codes(result)
    assert "RISK_EXPANSION" in node_scoped_codes(result)


@pytest.mark.acceptance("V02-P5-005")
def test_known_capability_inside_every_ceiling_is_accepted(tmp_path: Path) -> None:
    task, proposal = planned_proposal_using_the_known_capability(tmp_path)
    capabilities, policy = build_snapshots(allowed={KNOWN_CAPABILITY})

    result = GraphValidator().validate(proposal, capabilities, policy, task.envelope.budgets)

    assert result.errors == []
    assert result.status is GraphValidationStatus.ACCEPT


# --------------------------------------------------------------------------
# V02-P5-009 — completed protected nodes survive replan
# --------------------------------------------------------------------------


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Accretion Test"], check=True)
    (path / "README.md").write_text("M8 fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


async def setup_dynamic_run(
    tmp_path: Path,
) -> tuple[RunManager, DynamicWorkflowService, str, WorkflowProposal]:
    """A real dynamic run, paused with at least one SUCCEEDED protected node."""

    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime(step_delay=0.1)},
        limiter=ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
    )
    service = DynamicWorkflowService(manager, globally_enabled=True, operator_identity="m8-test")
    project = await manager.create_project("M8 replan", repository)
    features = await service.get_project_features(project.project_id)
    enabled = await service.update_project_features(
        project.project_id,
        dynamic_workflows=True,
        expected_revision=features.revision,
    )
    assert enabled.dynamic_workflows is True
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Preserve completed protected work across a replan.",
        task_patch={
            "task_type": "REVIEW",
            "required_outputs": [{"path": "README.md", "kind": "file"}],
        },
    )
    proposal = await service.propose(task.envelope.task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    outcome = await service.validate(proposal.run_id, proposal.proposal_id)
    assert outcome.validation.status is GraphValidationStatus.ACCEPT
    await service.activate(proposal.run_id, proposal.proposal_id)
    run_id = proposal.run_id

    for _ in range(400):
        events = await manager.store.list_events(run_id)
        if any(
            event.normalized_type is EventType.NODE_EXITED and event.node_id == f"{run_id}:start"
            for event in events
        ):
            break
        await asyncio.sleep(0.01)
    await manager.pause(run_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    paused = await manager.store.get_run(run_id)
    assert paused is not None and paused.state is RunState.PAUSED
    return manager, service, run_id, proposal


async def protected_node_key(manager: RunManager, run_id: str) -> str:
    graph = await manager.store.get_run_graph(run_id)
    checkpoint = await manager.store.get_latest_checkpoint(run_id)
    assert graph is not None
    assert checkpoint is not None, "the run must have checkpointed a node boundary"
    assert checkpoint.run_graph_id == graph.run_graph_id
    # The filters below mirror ``_assert_protected_nodes_preserved`` exactly:
    # that guard exempts node keys ending in ``-act`` / ``-observe`` from the
    # drop/rewrite conflict. For the shipped fragment the local id is literally
    # ``act``, so nothing is exempt here; the carve-out only bites for composed
    # graphs. Per ADR3-M8-005 the name-suffix carve-out is accepted for v0.3 and
    # a ``GraphNodeKind``-based protection rule is v0.4 work; the asymmetry with
    # ``_protected_state_refs`` (which applies no such exclusion) is pinned by
    # ``test_composed_act_node_is_excluded_from_the_conflict_guard_but_not_from_protected_refs``.
    succeeded = sorted(
        key
        for key, status in checkpoint.node_statuses.items()
        if status is GraphNodeStatus.SUCCEEDED
        and not key.endswith("-act")
        and not key.endswith("-observe")
    )
    assert succeeded, "at least one protected node must have SUCCEEDED"
    return succeeded[0]


@pytest.mark.acceptance("V02-P5-009")
async def test_replan_that_drops_a_succeeded_protected_node_is_rejected(
    tmp_path: Path,
) -> None:
    manager, service, run_id, proposal = await setup_dynamic_run(tmp_path)
    key = await protected_node_key(manager, run_id)
    previous = (await manager.store.list_graph_revisions(run_id))[-1]
    dropped = proposal.model_copy(
        update={"nodes": [node for node in proposal.nodes if node.local_id != key]}
    )

    with pytest.raises(DynamicWorkflowConflictError, match="remove or rewrite"):
        await service._assert_protected_nodes_preserved(run_id, previous, dropped)


@pytest.mark.acceptance("V02-P5-009")
async def test_replan_that_rewrites_a_succeeded_protected_node_is_rejected(
    tmp_path: Path,
) -> None:
    manager, service, run_id, proposal = await setup_dynamic_run(tmp_path)
    key = await protected_node_key(manager, run_id)
    previous = (await manager.store.list_graph_revisions(run_id))[-1]
    rewritten = proposal.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"objective": "Rewritten after completion."})
                if node.local_id == key
                else node
                for node in proposal.nodes
            ]
        }
    )

    with pytest.raises(DynamicWorkflowConflictError, match="remove or rewrite"):
        await service._assert_protected_nodes_preserved(run_id, previous, rewritten)


@pytest.mark.acceptance("V02-P5-009")
async def test_protected_node_reconciliation_does_not_depend_on_a_checkpoint(
    tmp_path: Path,
) -> None:
    """With no checkpoint, the run graph's own node statuses must still protect."""

    manager, service, run_id, proposal = await setup_dynamic_run(tmp_path)
    key = await protected_node_key(manager, run_id)
    previous = (await manager.store.list_graph_revisions(run_id))[-1]
    store = manager.store
    assert isinstance(store, MemoryStore)
    graph = await store.get_run_graph(run_id)
    assert graph is not None
    succeeded_node = next(node for node in graph.nodes if node.key == key)
    await store.update_run_graph(
        graph.run_graph_id,
        nodes=[succeeded_node.model_copy(update={"status": GraphNodeStatus.SUCCEEDED})],
        expected_revision=graph.graph_revision,
    )
    store.checkpoints[run_id] = []
    assert await store.get_latest_checkpoint(run_id) is None
    dropped = proposal.model_copy(
        update={"nodes": [node for node in proposal.nodes if node.local_id != key]}
    )

    with pytest.raises(DynamicWorkflowConflictError, match="remove or rewrite"):
        await service._assert_protected_nodes_preserved(run_id, previous, dropped)


@pytest.mark.acceptance("V02-P5-009")
async def test_replan_preserving_protected_nodes_persists_them_in_the_new_revision(
    tmp_path: Path,
) -> None:
    manager, service, run_id, proposal = await setup_dynamic_run(tmp_path)
    key = await protected_node_key(manager, run_id)
    previous = (await manager.store.list_graph_revisions(run_id))[-1]
    graph_before = await manager.store.get_run_graph(run_id)
    assert graph_before is not None
    protected_ref = next(node.node_id for node in graph_before.nodes if node.key == key)

    # (c) a proposal that preserves the completed node is accepted.
    await service._assert_protected_nodes_preserved(run_id, previous, proposal)

    replanned = await service.replan(
        run_id,
        reason=ReplanReason.HUMAN_REQUEST,
        evidence_refs=["operator:m8-inherited-replan"],
    )
    assert replanned.revision is not None
    assert replanned.revision.revision == previous.revision + 1
    assert protected_ref in replanned.revision.protected_state_refs

    resumed = manager.background.get(run_id)
    if resumed is not None:
        await resumed

    # Read the persisted state back rather than trusting the returned objects.
    stored_revision = await manager.store.get_graph_revision(run_id, previous.revision + 1)
    assert stored_revision is not None
    assert key in {node.local_id for node in stored_revision.nodes}
    assert protected_ref in stored_revision.protected_state_refs

    graph_after = await manager.store.get_run_graph(run_id)
    assert graph_after is not None
    statuses_after = {node.key: node.status for node in graph_after.nodes}
    assert statuses_after[key] is GraphNodeStatus.SUCCEEDED
    checkpoint_after = await manager.store.get_latest_checkpoint(run_id)
    assert checkpoint_after is not None
    assert checkpoint_after.node_statuses[key] is GraphNodeStatus.SUCCEEDED

    # The prior revision remains retrievable and unchanged.
    retained = await manager.store.get_graph_revision(run_id, previous.revision)
    assert retained is not None
    assert retained == previous


class TamperingValidator:
    """Wraps bounded repair so the ACCEPTed proposal tampers with a protected node.

    This forces ``replan()`` down its happy path with a proposal that deletes
    already-completed work, which is the only way to prove the guard is wired
    into the public replan flow rather than merely present on the service.
    """

    def __init__(
        self,
        service: DynamicWorkflowService,
        key: str,
        *,
        mode: str,
    ) -> None:
        self.service = service
        self.key = key
        self.mode = mode
        self.inner = service._validate_with_bounded_repair
        self.calls = 0

    async def __call__(self, proposal: WorkflowProposal) -> WorkflowValidationOutcome:
        self.calls += 1
        outcome = await self.inner(proposal)
        assert outcome.validation.status is GraphValidationStatus.ACCEPT
        accepted = outcome.proposal
        if self.mode == "drop":
            tampered = accepted.model_copy(
                update={
                    "nodes": [node for node in accepted.nodes if node.local_id != self.key],
                    "edges": [
                        edge
                        for edge in accepted.edges
                        if self.key not in {edge.source, edge.target}
                    ],
                }
            )
        else:
            tampered = accepted.model_copy(
                update={
                    "nodes": [
                        node.model_copy(update={"objective": "Rewritten after completion."})
                        if node.local_id == self.key
                        else node
                        for node in accepted.nodes
                    ]
                }
            )
        return outcome.model_copy(update={"proposal": tampered})


@pytest.mark.acceptance("V02-P5-009")
async def test_replan_rejects_a_proposal_that_drops_completed_work_through_the_public_api(
    tmp_path: Path,
) -> None:
    manager, service, run_id, _ = await setup_dynamic_run(tmp_path)
    key = await protected_node_key(manager, run_id)
    revisions_before = await manager.store.list_graph_revisions(run_id)
    previous = revisions_before[-1]
    graph_before = await manager.store.get_run_graph(run_id)
    assert graph_before is not None
    tampering = TamperingValidator(service, key, mode="drop")
    service._validate_with_bounded_repair = tampering  # type: ignore[method-assign]

    with pytest.raises(DynamicWorkflowConflictError, match="remove or rewrite"):
        await service.replan(
            run_id,
            reason=ReplanReason.HUMAN_REQUEST,
            evidence_refs=["operator:m8-inherited-replan-drop"],
        )

    assert tampering.calls == 1
    # The rejection is atomic: no new revision, and the live graph is untouched.
    assert await manager.store.get_graph_revision(run_id, previous.revision + 1) is None
    revisions_after = await manager.store.list_graph_revisions(run_id)
    assert len(revisions_after) == len(revisions_before)
    assert revisions_after[-1] == previous
    graph_after = await manager.store.get_run_graph(run_id)
    assert graph_after is not None
    assert graph_after.run_graph_id == graph_before.run_graph_id
    assert {node.key for node in graph_after.nodes} == {node.key for node in graph_before.nodes}
    assert {node.key for node in graph_after.nodes if node.status is GraphNodeStatus.SUCCEEDED} == {
        node.key for node in graph_before.nodes if node.status is GraphNodeStatus.SUCCEEDED
    }
    run_after = await manager.store.get_run(run_id)
    assert run_after is not None and run_after.state is RunState.PAUSED


@pytest.mark.acceptance("V02-P5-009")
async def test_replan_rejects_a_proposal_that_rewrites_completed_work_through_the_public_api(
    tmp_path: Path,
) -> None:
    """A rewrite still materializes, so only the guard can stop it."""

    manager, service, run_id, _ = await setup_dynamic_run(tmp_path)
    key = await protected_node_key(manager, run_id)
    revisions_before = await manager.store.list_graph_revisions(run_id)
    previous = revisions_before[-1]
    graph_before = await manager.store.get_run_graph(run_id)
    assert graph_before is not None
    tampering = TamperingValidator(service, key, mode="rewrite")
    service._validate_with_bounded_repair = tampering  # type: ignore[method-assign]

    with pytest.raises(DynamicWorkflowConflictError, match="remove or rewrite"):
        await service.replan(
            run_id,
            reason=ReplanReason.HUMAN_REQUEST,
            evidence_refs=["operator:m8-inherited-replan-rewrite"],
        )

    assert tampering.calls == 1
    assert await manager.store.get_graph_revision(run_id, previous.revision + 1) is None
    revisions_after = await manager.store.list_graph_revisions(run_id)
    assert len(revisions_after) == len(revisions_before)
    assert revisions_after[-1] == previous
    graph_after = await manager.store.get_run_graph(run_id)
    assert graph_after is not None
    assert graph_after.run_graph_id == graph_before.run_graph_id
    # The persisted revision still carries the original objective for the node.
    retained = await manager.store.get_graph_revision(run_id, previous.revision)
    assert retained is not None
    objectives_after = {node.local_id: node.objective for node in retained.nodes}
    assert objectives_after[key] != "Rewritten after completion."
    checkpoint_after = await manager.store.get_latest_checkpoint(run_id)
    assert checkpoint_after is not None
    assert checkpoint_after.node_statuses[key] is GraphNodeStatus.SUCCEEDED
    run_after = await manager.store.get_run(run_id)
    assert run_after is not None and run_after.state is RunState.PAUSED


@pytest.mark.acceptance("V02-P5-009")
async def test_composed_act_node_is_excluded_from_the_conflict_guard_but_not_from_protected_refs(
    tmp_path: Path,
) -> None:
    """Pins the accepted v0.3 asymmetry in the two protection rules (ADR3-M8-005).

    ``_assert_protected_nodes_preserved`` exempts keys ending in ``-act`` /
    ``-observe`` — a carve-out for the shipped fragment, whose *local* id is
    ``act`` and therefore never matches. In a composed graph the same key is
    spelled ``<fragment>-act``, so a SUCCEEDED node is silently droppable by a
    replan while ``_protected_state_refs`` still publishes it as protected
    state. This is accepted for v0.3, not desired: when the carve-out is
    replaced by a ``GraphNodeKind`` rule in v0.4 this test fails, and the ADR is
    revisited rather than the assertion relaxed.
    """

    manager, service, run_id, proposal = await setup_dynamic_run(tmp_path)
    protected = await protected_node_key(manager, run_id)
    previous = (await manager.store.list_graph_revisions(run_id))[-1]
    store = manager.store
    assert isinstance(store, MemoryStore)
    graph = await store.get_run_graph(run_id)
    assert graph is not None

    composed_key = "explore-act"
    assert composed_key not in {node.key for node in graph.nodes}
    template = next(node for node in graph.nodes if node.key == protected)
    composed = template.model_copy(
        update={
            "node_id": f"{run_id}:{composed_key}",
            "key": composed_key,
            "label": "Composed fragment act node",
            "status": GraphNodeStatus.SUCCEEDED,
        }
    )
    await store.replace_run_graph(
        graph.model_copy(update={"nodes": [*graph.nodes, composed]}),
        expected_revision=graph.graph_revision,
    )
    latest = await store.get_latest_checkpoint(run_id)
    assert latest is not None
    store.checkpoints[run_id] = [
        checkpoint
        if checkpoint.checkpoint_id != latest.checkpoint_id
        else checkpoint.model_copy(
            update={
                "node_statuses": {
                    **checkpoint.node_statuses,
                    composed_key: GraphNodeStatus.SUCCEEDED,
                }
            }
        )
        for checkpoint in store.checkpoints[run_id]
    ]
    reloaded = await store.get_latest_checkpoint(run_id)
    assert reloaded is not None
    assert reloaded.node_statuses[composed_key] is GraphNodeStatus.SUCCEEDED

    # (a) the conflict guard does NOT protect the composed ``-act`` node: a
    # proposal that never mentions it is accepted.
    assert composed_key not in {node.local_id for node in proposal.nodes}
    await service._assert_protected_nodes_preserved(run_id, previous, proposal)

    # (b) but the same node IS published as protected state.
    refs = await service._protected_state_refs(run_id)
    assert composed.node_id in refs

    # The carve-out is narrow: a SUCCEEDED node without the suffix still raises.
    dropped = proposal.model_copy(
        update={"nodes": [node for node in proposal.nodes if node.local_id != protected]}
    )
    with pytest.raises(DynamicWorkflowConflictError, match="remove or rewrite"):
        await service._assert_protected_nodes_preserved(run_id, previous, dropped)
