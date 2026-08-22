from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.contracts import (
    AuthMode,
    ExpectedHorizon,
    GraphEdgeKind,
    GraphNodeKind,
    Project,
    Provider,
    RiskLevel,
    RuntimeHealth,
    RuntimeStatus,
    Task,
    TaskBudgets,
    TaskEnvelope,
    TaskProfile,
    TaskType,
    UsagePressure,
)
from accretion.ids import has_prefix, new_id
from accretion.orchestration.condition_dsl import (
    ConditionEvaluationError,
    evaluate_condition,
)
from accretion.orchestration.fragments import FragmentWorkflowPlanner
from accretion.orchestration.materialize import materialize_workflow_template
from accretion.orchestration.models import (
    CapabilitySnapshot,
    ConditionOperator,
    DynamicWorkflowEdgeSpec,
    GraphValidationStatus,
    PlannerRuntime,
    PolicySnapshot,
    RuntimeRequirement,
    TypedCondition,
)
from accretion.orchestration.router import PerformanceAwareRuntimeRouter
from accretion.orchestration.validator import GraphValidator
from accretion.templates import validate_template


def task_and_profile(
    tmp_path: Path,
    *,
    risk: RiskLevel = RiskLevel.LOW,
    feedback: float = 0.2,
    parallelism: float = 0.2,
    parallel_runs: int = 1,
    task_type: TaskType = TaskType.REVIEW,
) -> tuple[Task, TaskProfile]:
    project = Project(
        project_id=new_id("project"),
        name="P5",
        repository_path=tmp_path,
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Exercise validated dynamic planning.",
            task_type=task_type,
            risk_level=risk,
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
        parallelism_potential=parallelism,
        uncertainty=0.5,
        verifier_strength=0.8,
        risk=risk,
        irreversible_actions=False,
        expected_horizon=ExpectedHorizon.MEDIUM,
        profile_confidence=0.9,
        semantic_rationale="P5 fixture",
    )
    return task, profile


def snapshots() -> tuple[CapabilitySnapshot, PolicySnapshot]:
    return (
        CapabilitySnapshot(
            verifiers={"output-contract", "git-diff", "trajectory-policy"},
            available_runtimes={
                Provider.CLAUDE,
                Provider.CODEX,
                Provider.DETERMINISTIC,
                Provider.FAKE,
            },
        ),
        PolicySnapshot(required_verifiers={"output-contract", "trajectory-policy"}),
    )


@pytest.mark.parametrize(
    ("kind", "prefix"),
    [
        ("workflow_proposal", "wfp"),
        ("graph_validation", "gvl"),
        ("graph_revision", "grv"),
        ("replan_request", "rpl"),
        ("runtime_decision", "rtd"),
    ],
)
def test_p5_identifiers_are_stable(kind: str, prefix: str) -> None:
    value = new_id(kind)
    assert value.startswith(f"{prefix}_")
    assert has_prefix(value, kind)


def test_condition_dsl_is_typed_bounded_and_fail_closed() -> None:
    condition = TypedCondition(
        operator=ConditionOperator.ALL,
        operands=[
            TypedCondition(
                operator=ConditionOperator.EQ,
                path="verifier.status",
                value="PASS",
            ),
            TypedCondition(
                operator=ConditionOperator.GT,
                path="budget.turns_remaining",
                value=0,
            ),
        ],
    )
    assert evaluate_condition(
        condition,
        {"verifier": {"status": "PASS"}, "budget": {"turns_remaining": 3}},
    )
    with pytest.raises(ConditionEvaluationError, match="unavailable"):
        evaluate_condition(condition, {"verifier": {"status": "PASS"}})
    forbidden = TypedCondition(
        operator=ConditionOperator.EQ,
        path="python.eval",
        value=True,
    )
    with pytest.raises(ConditionEvaluationError, match="not allowed"):
        evaluate_condition(forbidden, {"python": {"eval": True}})
    with pytest.raises(ValidationError):
        TypedCondition(operator=ConditionOperator.NOT, operands=[])


def test_accepted_proposal_materializes_repeatably_into_executor_grammar(
    tmp_path: Path,
) -> None:
    task, profile = task_and_profile(tmp_path, feedback=0.8)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    capabilities, policy = snapshots()
    validation = GraphValidator().validate(
        proposal, capabilities, policy, task.envelope.budgets
    )
    assert validation.status is GraphValidationStatus.ACCEPT
    assert validation.normalized_graph_hash is not None

    first = materialize_workflow_template(
        proposal, normalized_graph_hash=validation.normalized_graph_hash
    )
    second = materialize_workflow_template(
        proposal, normalized_graph_hash=validation.normalized_graph_hash
    )

    assert first.template_id == second.template_id
    assert first.checksum == second.checksum
    assert validate_template(first) == []
    assert {node.key for node in first.nodes} >= {
        "repair-loop",
        "repair-loop-act",
        "repair-loop-observe",
    }
    assert any(edge.key == "verify-fallback-inconclusive" for edge in first.edges)


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({}, "single-act-verify@1.0.0"),
        ({"feedback": 0.8}, "bounded-repair@1.0.0"),
        (
            {"parallelism": 0.9, "parallel_runs": 2},
            "dual-analysis-join@1.0.0",
        ),
        ({"risk": RiskLevel.HIGH}, "approval-gated-change@1.0.0"),
    ],
)
def test_fragment_planner_selects_reviewed_structure(
    tmp_path: Path, kwargs: dict[str, object], fragment: str
) -> None:
    task, profile = task_and_profile(tmp_path, **kwargs)  # type: ignore[arg-type]
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    assert proposal.fragment_refs == [fragment]
    assert proposal.planner_runtime is PlannerRuntime.DETERMINISTIC
    assert proposal.confidence == 0.9
    assert len(proposal.nodes) <= 32


def test_valid_fragment_is_repeatable_and_accepted(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path)
    planner = FragmentWorkflowPlanner()
    proposal = planner.propose(task, profile)
    capability_snapshot, policy_snapshot = snapshots()
    validator = GraphValidator()
    first = validator.validate(
        proposal,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    second = validator.validate(
        proposal,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert first.status is GraphValidationStatus.ACCEPT
    assert first.normalized_graph_hash == second.normalized_graph_hash
    assert first.normalized_graph_hash == validator.normalized_hash(proposal)


def test_validator_rejects_capability_and_privilege_expansion(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    act = next(node for node in proposal.nodes if node.kind is GraphNodeKind.AGENT)
    tampered = proposal.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"capability_refs": ["external.publish"]})
                if node.local_id == act.local_id
                else node
                for node in proposal.nodes
            ],
            "required_capabilities": ["external.publish"],
        }
    )
    capability_snapshot, policy_snapshot = snapshots()
    result = GraphValidator().validate(
        tampered,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert result.status is GraphValidationStatus.REJECT
    assert "UNKNOWN_CAPABILITY" in {finding.code for finding in result.errors}


def test_validator_rejects_unbounded_cycle_and_verifier_bypass(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    tampered = proposal.model_copy(
        update={
            "edges": [edge for edge in proposal.edges if edge.source != "verify"]
            + [
                DynamicWorkflowEdgeSpec(
                    local_id="verify-act",
                    source="verify",
                    target="act",
                    kind=GraphEdgeKind.NORMAL,
                ),
                DynamicWorkflowEdgeSpec(
                    local_id="act-complete",
                    source="act",
                    target="complete",
                ),
            ]
        }
    )
    capability_snapshot, policy_snapshot = snapshots()
    result = GraphValidator().validate(
        tampered,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    codes = {finding.code for finding in result.errors}
    assert result.status is GraphValidationStatus.REJECT
    assert "UNBOUNDED_CYCLE" in codes
    assert "VERIFIER_BYPASS" in codes


def test_high_risk_graph_cannot_bypass_approval(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path, risk=RiskLevel.HIGH)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    proposal = proposal.model_copy(
        update={
            "edges": [edge for edge in proposal.edges if edge.source not in {"start", "verify"}]
            + [
                DynamicWorkflowEdgeSpec(
                    local_id="start-act-direct",
                    source="start",
                    target="act",
                ),
                DynamicWorkflowEdgeSpec(
                    local_id="verify-complete-direct",
                    source="verify",
                    target="complete",
                ),
            ]
        }
    )
    capability_snapshot, policy_snapshot = snapshots()
    result = GraphValidator().validate(
        proposal,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert "APPROVAL_BYPASS" in {finding.code for finding in result.errors}


def test_protected_node_requires_gate_before_execution(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path, risk=RiskLevel.HIGH)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    proposal = proposal.model_copy(
        update={
            "nodes": [
                node for node in proposal.nodes if node.local_id != "approve-plan"
            ],
            "edges": [
                DynamicWorkflowEdgeSpec(
                    local_id="start-act",
                    source="start",
                    target="act",
                ),
                *[
                    edge
                    for edge in proposal.edges
                    if edge.local_id not in {"start-approval", "approval-act"}
                ],
            ],
            "expected_approval_gates": ["approve-outcome"],
        }
    )
    capability_snapshot, policy_snapshot = snapshots()
    result = GraphValidator().validate(
        proposal,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert "APPROVAL_BEFORE_PROTECTED_NODE" in {
        finding.code for finding in result.errors
    }


def test_provider_specific_node_must_match_fixed_p5_runtime(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    proposal = proposal.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={"runtime_requirement": RuntimeRequirement.CODEX}
                )
                if node.local_id == "act"
                else node
                for node in proposal.nodes
            ]
        }
    )
    capability_snapshot, policy_snapshot = snapshots()
    policy_snapshot = policy_snapshot.model_copy(
        update={"execution_runtime": Provider.FAKE}
    )
    result = GraphValidator().validate(
        proposal,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert "RUNTIME_INCOMPATIBLE" in {finding.code for finding in result.errors}


def test_contract_mismatch_is_repairable_once(tmp_path: Path) -> None:
    task, profile = task_and_profile(tmp_path)
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    start = proposal.nodes[0].model_copy(
        update={"output_contract": {"type": "object", "properties": {}}}
    )
    act = proposal.nodes[1].model_copy(
        update={
            "input_contract": {
                "type": "object",
                "properties": {"seed": {"type": "string"}},
                "required": ["seed"],
            }
        }
    )
    proposal = proposal.model_copy(update={"nodes": [start, act, *proposal.nodes[2:]]})
    capability_snapshot, policy_snapshot = snapshots()
    first = GraphValidator().validate(
        proposal,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert first.status is GraphValidationStatus.REPAIRABLE
    repaired = FragmentWorkflowPlanner().repair(proposal)
    second = GraphValidator().validate(
        repaired,
        capability_snapshot,
        policy_snapshot,
        task.envelope.budgets,
    )
    assert second.status is GraphValidationStatus.REJECT
    with pytest.raises(ValueError, match="exhausted"):
        FragmentWorkflowPlanner().repair(repaired)


def runtime_health(
    provider: Provider,
    *,
    version: str,
    status: RuntimeStatus,
    pressure: UsagePressure,
) -> RuntimeHealth:
    return RuntimeHealth(
        runtime_id=f"{provider.value.lower()}-runtime",
        provider=provider,
        status=status,
        auth_mode=AuthMode.LOCAL,
        runtime_version=version,
        observed_usage_pressure=pressure,
    )


def test_runtime_router_is_interpretable_version_keyed_and_fail_closed() -> None:
    health = [
        runtime_health(
            Provider.CODEX,
            version="0.148.0",
            status=RuntimeStatus.READY,
            pressure=UsagePressure.LOW,
        ),
        runtime_health(
            Provider.CLAUDE,
            version="2.1.239",
            status=RuntimeStatus.RATE_LIMITED,
            pressure=UsagePressure.HIGH,
        ),
    ]
    decision = PerformanceAwareRuntimeRouter().decide(
        run_id=new_id("run"),
        node_id="node:act",
        health=health,
        historical_quality={(Provider.CODEX, "0.148.0"): 0.9},
    )
    assert decision.selected_runtime is Provider.CODEX
    assert decision.fallback_order == []
    claude = next(item for item in decision.candidates if item.provider is Provider.CLAUDE)
    assert not claude.available
    assert claude.exclusion_reason == "runtime status is RATE_LIMITED"
