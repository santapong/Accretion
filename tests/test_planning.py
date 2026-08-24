from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    ExecutionMode,
    ExpectedHorizon,
    Project,
    PromptContract,
    RiskLevel,
    Task,
    TaskEnvelope,
    TaskProfile,
    TaskType,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.planning import evaluate_override, profile_task, select_strategy
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager


def profile(**updates: object) -> TaskProfile:
    values: dict[str, object] = {
        "profile_id": new_id("profile"),
        "task_id": new_id("task"),
        "complexity": 0.5,
        "structure_certainty": 0.5,
        "feedback_dependency": 0.5,
        "dependency_complexity": 0.5,
        "parallelism_potential": 0.2,
        "uncertainty": 0.2,
        "verifier_strength": 0.5,
        "risk": RiskLevel.LOW,
        "irreversible_actions": False,
        "expected_horizon": ExpectedHorizon.MEDIUM,
        "profile_confidence": 1.0,
        "semantic_rationale": "fixture",
    }
    values.update(updates)
    return TaskProfile.model_validate(values)


def manager(store: MemoryStore, tmp_path: Path) -> RunManager:
    return RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )


@pytest.mark.acceptance("V01-P1-001")
def test_versioned_contract_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        PromptContract.model_validate(
            {
                "schema_version": "2.0",
                "prompt_contract_id": new_id("prompt"),
                "task_id": new_id("task"),
                "role": "agent",
                "objective": "test",
            }
        )


@pytest.mark.acceptance("V01-P1-002")
def test_profiler_is_deterministic_and_uses_explicit_unknowns(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (tmp_path / "tests").mkdir()
    project = Project(project_id=new_id("project"), name="fixture", repository_path=tmp_path)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Objective words are deliberately irrelevant to profiling.",
            task_type=TaskType.IMPLEMENT,
            success_criteria=["tests pass"],
        )
    )
    first = profile_task(task, project)
    second = profile_task(task, project)
    assert first.model_dump(exclude={"profile_id", "created_at"}) == second.model_dump(
        exclude={"profile_id", "created_at"}
    )
    assert first.verifier_strength == 0.9
    assert first.unknown_features == []
    objective_changed = profile_task(
        Task(envelope=task.envelope.model_copy(update={"objective": "LOOP GRAPH urgent"})),
        project,
    )
    assert first.model_dump(exclude={"profile_id", "created_at"}) == objective_changed.model_dump(
        exclude={"profile_id", "created_at"}
    )

    unknown_task = Task(envelope=task.envelope.model_copy(update={"task_type": TaskType.OTHER}))
    unknown = profile_task(unknown_task, project)
    assert unknown.complexity is None
    assert "complexity" in unknown.unknown_features
    fallback = select_strategy(unknown)
    assert fallback.selected_template_id == "safe-unknown-v1"

    low_confidence = select_strategy(profile(profile_confidence=0.5))
    assert low_confidence.selected_template_id == "safe-unknown-v1"


@pytest.mark.parametrize(
    ("fixture", "mode", "template"),
    [
        (
            profile(complexity=0.1, feedback_dependency=0.1, dependency_complexity=0.1),
            ExecutionMode.DIRECT,
            "direct-v1",
        ),
        (
            profile(structure_certainty=0.8, feedback_dependency=0.2),
            ExecutionMode.GRAPH,
            "fixed-graph-v1",
        ),
        (
            profile(feedback_dependency=0.7, dependency_complexity=0.2),
            ExecutionMode.LOOP,
            "feedback-loop-v1",
        ),
        (profile(), ExecutionMode.HYBRID, "hybrid-rd-v1"),
    ],
)
@pytest.mark.acceptance("V01-P1-003")
def test_selector_covers_all_threshold_branches(
    fixture: TaskProfile, mode: ExecutionMode, template: str
) -> None:
    decision = select_strategy(fixture)
    assert decision.selected_mode is mode
    assert decision.selected_template_id == template


@pytest.mark.acceptance("V01-P1-005")
def test_high_risk_policy_forces_gates_and_denies_downgrade() -> None:
    risky = profile(risk=RiskLevel.HIGH)
    current = select_strategy(risky)
    assert current.selected_mode is ExecutionMode.GRAPH
    assert current.requires_approval is True
    assert current.requires_independent_verifier is True

    override, replacement = evaluate_override(
        profile=risky,
        current=current,
        requested_mode=ExecutionMode.DIRECT,
        requested_template_id="direct-v1",
        reason="operator preference",
        operator_identity="local-operator",
    )
    assert override.accepted is False
    assert replacement is None
    assert override.denial_reason


@pytest.mark.acceptance("V01-P1-001", "V01-P1-006")
async def test_task_planning_is_atomic_legacy_safe_and_override_audited(
    tmp_path: Path,
) -> None:
    store = MemoryStore()
    run_manager = manager(store, tmp_path)
    project = await run_manager.create_project("fixture", tmp_path)
    created = await run_manager.create_task(
        project_id=project.project_id,
        objective="Review the supplied change.",
        task_patch={"task_type": TaskType.REVIEW},
    )
    assert created.prompt_contract_id
    assert created.context_bundle_id
    planning = await run_manager.get_task_planning(created.envelope.task_id)
    assert planning.current_decision.selected_mode is ExecutionMode.DIRECT

    result = await run_manager.override_strategy(
        task_id=created.envelope.task_id,
        requested_mode=ExecutionMode.GRAPH,
        requested_template_id="fixed-graph-v1",
        reason="Use explicit review stages.",
    )
    assert result.override.accepted is True
    history = await run_manager.get_task_planning(created.envelope.task_id)
    assert len(history.decision_history) == 2
    assert len(history.override_history) == 1

    legacy = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Legacy task",
            task_type=TaskType.REVIEW,
        )
    )
    await store.create_task(legacy)
    legacy_planning = await run_manager.get_task_planning(legacy.envelope.task_id)
    assert legacy_planning.prompt_contract.task_id == legacy.envelope.task_id
    assert (await store.get_task(legacy.envelope.task_id)).current_profile_id  # type: ignore[union-attr]
