import pytest

from accretion.contracts import (
    AcceptancePolicy,
    LoopBudgetRemaining,
    LoopExecutionStatus,
    LoopState,
    LoopStopReason,
    RunState,
    TaskBudgets,
    TaskEnvelope,
    VerificationResult,
    VerificationStatus,
)
from accretion.looping import (
    build_loop_execution,
    build_loop_spec,
    evaluate_acceptance,
    terminal_outcome,
)


def result(verifier_id: str, status: VerificationStatus) -> VerificationResult:
    return VerificationResult(
        verification_id=f"ver_{verifier_id}",
        run_id="run_fixture",
        verifier_id=verifier_id,
        verifier_version="fixture-v1",
        target_ref="art_fixture",
        status=status,
    )


def policy(*required: str, allow_inconclusive: bool = False) -> AcceptancePolicy:
    return AcceptancePolicy(
        policy_id="acp_fixture",
        required_verifiers=list(required),
        allow_inconclusive=allow_inconclusive,
    )


def test_acceptance_is_fail_closed_and_requires_every_verifier() -> None:
    acceptance = policy("tests", "outputs")
    assert evaluate_acceptance(acceptance, [result("tests", VerificationStatus.PASS)]) is (
        VerificationStatus.INCONCLUSIVE
    )
    assert evaluate_acceptance(
        acceptance,
        [
            result("tests", VerificationStatus.PASS),
            result("outputs", VerificationStatus.FAIL),
        ],
    ) is VerificationStatus.FAIL
    assert evaluate_acceptance(
        acceptance,
        [
            result("tests", VerificationStatus.PASS),
            result("outputs", VerificationStatus.PASS),
        ],
    ) is VerificationStatus.PASS


@pytest.mark.acceptance("V01-P2-005")
def test_inconclusive_requires_an_explicit_policy_exception() -> None:
    inconclusive = [result("tests", VerificationStatus.INCONCLUSIVE)]
    assert evaluate_acceptance(policy("tests"), inconclusive) is VerificationStatus.INCONCLUSIVE
    assert evaluate_acceptance(
        policy("tests", allow_inconclusive=True), inconclusive
    ) is VerificationStatus.PASS


def test_loop_spec_uses_task_ceiling_without_resetting_budgets() -> None:
    envelope = TaskEnvelope(
        task_id="tsk_fixture",
        project_id="prj_fixture",
        objective="Repair until verified.",
        budgets=TaskBudgets(
            wall_time_seconds=90,
            max_turns=4,
            max_tool_calls=12,
            max_loop_iterations=3,
        ),
    )
    spec = build_loop_spec(envelope, ["tests"])
    execution = build_loop_execution(run_id="run_fixture", spec=spec, policy=policy("tests"))
    assert execution.state.budget_remaining == LoopBudgetRemaining(
        wall_time_seconds=90,
        turns=4,
        tool_calls=12,
        iterations=3,
    )


def test_terminal_outcome_prioritizes_verified_success_then_budgets() -> None:
    envelope = TaskEnvelope(
        task_id="tsk_fixture",
        project_id="prj_fixture",
        objective="Repair until verified.",
        budgets=TaskBudgets(max_loop_iterations=1),
    )
    execution = build_loop_execution(
        run_id="run_fixture",
        spec=build_loop_spec(envelope, ["tests"]),
        policy=policy("tests"),
    )
    execution = execution.model_copy(
        update={
            "state": LoopState(
                iteration=1,
                budget_remaining=LoopBudgetRemaining(
                    wall_time_seconds=10,
                    turns=1,
                    tool_calls=1,
                    iterations=0,
                ),
            )
        }
    )
    assert terminal_outcome(execution, VerificationStatus.PASS) == (
        LoopExecutionStatus.SUCCEEDED,
        LoopStopReason.VERIFIED_SUCCESS,
        RunState.SUCCEEDED,
    )
    assert terminal_outcome(execution, VerificationStatus.FAIL) == (
        LoopExecutionStatus.REQUIRES_HUMAN,
        LoopStopReason.MAX_ITERATIONS,
        RunState.REQUIRES_HUMAN,
    )


def test_inconclusive_escalates_instead_of_accepting() -> None:
    envelope = TaskEnvelope(
        task_id="tsk_fixture",
        project_id="prj_fixture",
        objective="Produce evidence.",
    )
    execution = build_loop_execution(
        run_id="run_fixture",
        spec=build_loop_spec(envelope, ["outputs"]),
        policy=policy("outputs"),
    )
    assert terminal_outcome(execution, VerificationStatus.INCONCLUSIVE) == (
        LoopExecutionStatus.REQUIRES_HUMAN,
        LoopStopReason.POLICY_ESCALATION,
        RunState.REQUIRES_HUMAN,
    )


@pytest.mark.parametrize(
    ("remaining", "state_patch", "reason", "loop_status", "run_state"),
    [
        (
            LoopBudgetRemaining(
                wall_time_seconds=0, turns=3, tool_calls=3, iterations=3
            ),
            {},
            LoopStopReason.WALL_TIME_EXCEEDED,
            LoopExecutionStatus.REQUIRES_HUMAN,
            RunState.REQUIRES_HUMAN,
        ),
        (
            LoopBudgetRemaining(
                wall_time_seconds=30, turns=3, tool_calls=0, iterations=3
            ),
            {},
            LoopStopReason.MAX_TOOL_CALLS,
            LoopExecutionStatus.REQUIRES_HUMAN,
            RunState.REQUIRES_HUMAN,
        ),
        (
            LoopBudgetRemaining(
                wall_time_seconds=30, turns=0, tool_calls=3, iterations=3
            ),
            {},
            LoopStopReason.MAX_TURNS,
            LoopExecutionStatus.REQUIRES_HUMAN,
            RunState.REQUIRES_HUMAN,
        ),
        (
            LoopBudgetRemaining(
                wall_time_seconds=30, turns=3, tool_calls=3, iterations=0
            ),
            {},
            LoopStopReason.MAX_ITERATIONS,
            LoopExecutionStatus.REQUIRES_HUMAN,
            RunState.REQUIRES_HUMAN,
        ),
        (
            LoopBudgetRemaining(
                wall_time_seconds=30, turns=3, tool_calls=3, iterations=3
            ),
            {"consecutive_no_progress": 2},
            LoopStopReason.NO_PROGRESS,
            LoopExecutionStatus.REQUIRES_HUMAN,
            RunState.REQUIRES_HUMAN,
        ),
        (
            LoopBudgetRemaining(
                wall_time_seconds=30, turns=3, tool_calls=3, iterations=3
            ),
            {"repeated_failure_count": 2},
            LoopStopReason.REPEATED_FAILURE,
            LoopExecutionStatus.REQUIRES_HUMAN,
            RunState.REQUIRES_HUMAN,
        ),
        (
            LoopBudgetRemaining(
                wall_time_seconds=30, turns=3, tool_calls=3, iterations=3
            ),
            {"provider_failure_count": 2},
            LoopStopReason.PROVIDER_FAILURE,
            LoopExecutionStatus.FAILED,
            RunState.FAILED,
        ),
    ],
)
@pytest.mark.acceptance("V01-P2-001")
def test_every_bounded_stop_condition_is_explicit(
    remaining: LoopBudgetRemaining,
    state_patch: dict[str, int],
    reason: LoopStopReason,
    loop_status: LoopExecutionStatus,
    run_state: RunState,
) -> None:
    envelope = TaskEnvelope(
        task_id="tsk_fixture",
        project_id="prj_fixture",
        objective="Remain bounded.",
        budgets=TaskBudgets(
            wall_time_seconds=30,
            max_turns=3,
            max_tool_calls=3,
            max_loop_iterations=3,
        ),
    )
    execution = build_loop_execution(
        run_id="run_fixture",
        spec=build_loop_spec(envelope, ["tests"]),
        policy=policy("tests"),
    )
    execution = execution.model_copy(
        update={
            "state": LoopState(
                budget_remaining=remaining,
                **state_patch,
            )
        }
    )
    assert terminal_outcome(execution, VerificationStatus.FAIL) == (
        loop_status,
        reason,
        run_state,
    )
