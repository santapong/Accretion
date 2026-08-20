"""Pure crash-reconciliation classification (SDD v0.1 §12.4).

`evaluate_checkpoint` and `classify_run` take only durable facts and return
data; the run manager applies the resulting actions. A checkpoint is a
consistency marker over the authoritative rows, never a second source of
truth: authority ahead of the cut is merely stale, authority behind the cut
is corruption and fails closed to human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from accretion.contracts import Checkpoint, LoopExecution


class ReconcileClassification(StrEnum):
    RESUMABLE = "resumable"
    RECREATE = "recreate"
    TERMINAL = "terminal"
    REQUIRES_HUMAN = "requires-human"


class CheckpointInvalidReason(StrEnum):
    SEQUENCE_AHEAD_OF_LOG = "SEQUENCE_AHEAD_OF_LOG"
    LOOP_AUTHORITY_BEHIND_CHECKPOINT = "LOOP_AUTHORITY_BEHIND_CHECKPOINT"
    WORKSPACE_REVISION_CONFLICT = "WORKSPACE_REVISION_CONFLICT"
    WORKSPACE_LEASE_MISSING = "WORKSPACE_LEASE_MISSING"


@dataclass(frozen=True, slots=True)
class CheckpointEvaluation:
    valid: bool
    stale: bool = False
    reason: CheckpointInvalidReason | None = None


def evaluate_checkpoint(
    checkpoint: Checkpoint,
    *,
    run_last_sequence: int,
    loop_executions: dict[str, LoopExecution],
    workspace_status: str | None,
) -> CheckpointEvaluation:
    """Judge the latest checkpoint against the authoritative rows.

    workspace_status is WorktreeManager.inspect output ("CONSISTENT",
    "MISSING", "INVALID", "REVISION_CONFLICT"), or None when the checkpoint
    recorded no workspace binding.
    """

    if checkpoint.sequence > run_last_sequence:
        # Impossible under the atomic commit; its presence is corruption.
        return CheckpointEvaluation(
            valid=False, reason=CheckpointInvalidReason.SEQUENCE_AHEAD_OF_LOG
        )
    stale = False
    for cursor in checkpoint.loop_cursors:
        execution = loop_executions.get(cursor.loop_execution_id)
        if execution is None or execution.state.iteration < cursor.iteration:
            return CheckpointEvaluation(
                valid=False,
                reason=CheckpointInvalidReason.LOOP_AUTHORITY_BEHIND_CHECKPOINT,
            )
        if execution.state.iteration > cursor.iteration:
            stale = True
    if checkpoint.workspace_lease_id is not None:
        if workspace_status is None:
            return CheckpointEvaluation(
                valid=False, reason=CheckpointInvalidReason.WORKSPACE_LEASE_MISSING
            )
        if workspace_status == "REVISION_CONFLICT":
            return CheckpointEvaluation(
                valid=False, reason=CheckpointInvalidReason.WORKSPACE_REVISION_CONFLICT
            )
    return CheckpointEvaluation(valid=True, stale=stale)


@dataclass(frozen=True, slots=True)
class RunClassification:
    classification: ReconcileClassification
    reason: str
    checkpoint_reason: CheckpointInvalidReason | None = None


def classify_run(
    *,
    workspace_status: str | None,
    has_uncertain_side_effects: bool,
    has_candidate_work: bool,
    checkpoint_evaluation: CheckpointEvaluation | None,
) -> RunClassification:
    """The §12.4 four-way classification for a non-terminal crashed run.

    Never blindly retry a side effect: any uncertain durable side effect
    escalates. A lost workspace with nothing in it is recreate; a lost
    workspace holding candidate work needs a human.
    """

    if has_uncertain_side_effects:
        return RunClassification(
            classification=ReconcileClassification.REQUIRES_HUMAN,
            reason="uncertain side-effect operations must be resolved by a human",
        )
    if checkpoint_evaluation is not None and not checkpoint_evaluation.valid:
        if (
            checkpoint_evaluation.reason is CheckpointInvalidReason.WORKSPACE_LEASE_MISSING
            and not has_candidate_work
            and workspace_status != "CONSISTENT"
        ):
            return RunClassification(
                classification=ReconcileClassification.RECREATE,
                reason="workspace substrate lost with no candidate work to lose",
                checkpoint_reason=checkpoint_evaluation.reason,
            )
        return RunClassification(
            classification=ReconcileClassification.REQUIRES_HUMAN,
            reason="the last checkpoint is invalid",
            checkpoint_reason=checkpoint_evaluation.reason,
        )
    if workspace_status is not None and workspace_status != "CONSISTENT":
        # A diverged (REVISION_CONFLICT) worktree is candidate work by
        # definition; only a fully absent substrate qualifies for recreate.
        if has_candidate_work or workspace_status == "REVISION_CONFLICT":
            return RunClassification(
                classification=ReconcileClassification.REQUIRES_HUMAN,
                reason="workspace is unrecoverable and candidate work would be lost",
            )
        return RunClassification(
            classification=ReconcileClassification.RECREATE,
            reason="workspace substrate lost with no candidate work to lose",
        )
    return RunClassification(
        classification=ReconcileClassification.RESUMABLE,
        reason="durable state is coherent; resume from the last valid checkpoint",
    )
