from __future__ import annotations

from datetime import UTC, datetime

from accretion.contracts import (
    AcceptancePolicy,
    LoopBudgetRemaining,
    LoopExecution,
    LoopExecutionStatus,
    LoopSpec,
    LoopState,
    LoopStopReason,
    RunState,
    TaskEnvelope,
    VerificationResult,
    VerificationStatus,
)
from accretion.ids import new_id


def build_loop_spec(envelope: TaskEnvelope, verifier_refs: list[str]) -> LoopSpec:
    budgets = envelope.budgets
    return LoopSpec(
        loop_id=new_id("loop"),
        max_iterations=budgets.max_loop_iterations,
        max_wall_time_seconds=budgets.wall_time_seconds,
        max_tool_calls=budgets.max_tool_calls,
        max_turns=budgets.max_turns,
        verifier_refs=verifier_refs,
    )


def build_loop_execution(
    *, run_id: str, spec: LoopSpec, policy: AcceptancePolicy
) -> LoopExecution:
    return LoopExecution(
        loop_execution_id=new_id("loop_execution"),
        run_id=run_id,
        spec=spec,
        acceptance_policy_ref=policy.policy_id,
        acceptance_policy=policy,
        state=LoopState(
            budget_remaining=LoopBudgetRemaining(
                wall_time_seconds=spec.max_wall_time_seconds,
                tool_calls=spec.max_tool_calls,
                turns=spec.max_turns,
                iterations=spec.max_iterations,
            )
        ),
    )


def evaluate_acceptance(
    policy: AcceptancePolicy, results: list[VerificationResult]
) -> VerificationStatus:
    """Apply a fail-closed acceptance policy to immutable verifier results."""

    latest = {result.verifier_id: result for result in results}
    if any(verifier_id not in latest for verifier_id in policy.required_verifiers):
        return VerificationStatus.INCONCLUSIVE
    required = [latest[verifier_id] for verifier_id in policy.required_verifiers]
    if not required:
        return VerificationStatus.INCONCLUSIVE
    if any(result.status is VerificationStatus.FAIL for result in required):
        return VerificationStatus.FAIL
    for result in required:
        threshold = policy.score_thresholds.get(result.verifier_id)
        if threshold is not None and (result.score is None or result.score < threshold):
            return VerificationStatus.FAIL
    if any(result.status is VerificationStatus.INCONCLUSIVE for result in required):
        return (
            VerificationStatus.PASS
            if policy.allow_inconclusive
            else VerificationStatus.INCONCLUSIVE
        )
    return VerificationStatus.PASS


def terminal_outcome(
    execution: LoopExecution,
    acceptance: VerificationStatus,
) -> tuple[LoopExecutionStatus, LoopStopReason, RunState] | None:
    state = execution.state
    remaining = state.budget_remaining
    if acceptance is VerificationStatus.PASS:
        return (
            LoopExecutionStatus.SUCCEEDED,
            LoopStopReason.VERIFIED_SUCCESS,
            RunState.SUCCEEDED,
        )
    if acceptance is VerificationStatus.INCONCLUSIVE:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.POLICY_ESCALATION,
            RunState.REQUIRES_HUMAN,
        )
    if state.provider_failure_count >= execution.spec.provider_failure_threshold:
        return (
            LoopExecutionStatus.FAILED,
            LoopStopReason.PROVIDER_FAILURE,
            RunState.FAILED,
        )
    if remaining.wall_time_seconds <= 0:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.WALL_TIME_EXCEEDED,
            RunState.REQUIRES_HUMAN,
        )
    if remaining.tool_calls <= 0:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.MAX_TOOL_CALLS,
            RunState.REQUIRES_HUMAN,
        )
    if remaining.turns <= 0:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.MAX_TURNS,
            RunState.REQUIRES_HUMAN,
        )
    if remaining.iterations <= 0:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.MAX_ITERATIONS,
            RunState.REQUIRES_HUMAN,
        )
    if state.consecutive_no_progress >= execution.spec.no_progress_window:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.NO_PROGRESS,
            RunState.REQUIRES_HUMAN,
        )
    if state.repeated_failure_count >= execution.spec.repeated_failure_threshold:
        return (
            LoopExecutionStatus.REQUIRES_HUMAN,
            LoopStopReason.REPEATED_FAILURE,
            RunState.REQUIRES_HUMAN,
        )
    return None


def completed_execution(
    execution: LoopExecution,
    *,
    status: LoopExecutionStatus,
    stop_reason: LoopStopReason,
) -> LoopExecution:
    now = datetime.now(UTC)
    return execution.model_copy(
        update={
            "status": status,
            "stop_reason": stop_reason,
            "updated_at": now,
            "completed_at": now,
        }
    )
