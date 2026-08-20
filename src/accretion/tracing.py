"""Replay: reconstruct the execution trace from immutable events only.

`build_execution_trace` is a pure fold over the append-only event log — it
imports no store and reads no mutable state, so re-running it after a backend
restart reproduces the same trace (V01-P3-004). Evidence never guesses
optimistically: an unreadable node exit status folds to FAILED.
"""

from __future__ import annotations

from collections.abc import Iterable

from accretion.contracts import (
    AgentEvent,
    ApprovalTraceRef,
    CheckpointKind,
    CheckpointTraceRef,
    EventType,
    ExecutionTrace,
    GraphNodeStatus,
    LoopIterationTraceRef,
    NodeTraversal,
    Run,
    RunState,
    RuntimeCallTraceRef,
    ToolCallTraceRef,
    VerificationStatus,
    VerificationTraceRef,
)

_RUNTIME_CALL_TERMINALS: dict[EventType, str] = {
    EventType.RUNTIME_CALL_COMPLETED: "COMPLETED",
    EventType.RUNTIME_CALL_FAILED: "FAILED",
    EventType.RUNTIME_CALL_CANCELLED: "CANCELLED",
}

_TOOL_STATUS: dict[EventType, str] = {
    EventType.TOOL_REQUESTED: "REQUESTED",
    EventType.TOOL_STARTED: "STARTED",
    EventType.TOOL_COMPLETED: "COMPLETED",
    EventType.TOOL_FAILED: "FAILED",
}

_TERMINAL_EVENT_STATES: dict[EventType, RunState] = {
    EventType.RUN_COMPLETED: RunState.SUCCEEDED,
    EventType.RUN_FAILED: RunState.FAILED,
    EventType.RUN_CANCELLED: RunState.CANCELLED,
}


def tool_call_key(event: AgentEvent) -> str:
    """Stable identity for a tool call across its REQUESTED/STARTED/terminal events."""

    for key in ("tool_call_id", "native_request_id", "call_id", "id"):
        value = event.payload.get(key)
        if value:
            return str(value)
    extension = event.payload.get("provider_extension")
    if isinstance(extension, dict):
        for key in ("tool_call_id", "native_request_id", "call_id", "id"):
            value = extension.get(key)
            if value:
                return str(value)
        item = extension.get("item")
        if isinstance(item, dict):
            for key in ("tool_call_id", "call_id", "id"):
                value = item.get(key)
                if value:
                    return str(value)
    return event.event_id


def _exit_status(value: object) -> GraphNodeStatus:
    try:
        return GraphNodeStatus(str(value))
    except ValueError:
        return GraphNodeStatus.FAILED


def _verification_status(value: object) -> VerificationStatus | None:
    try:
        return VerificationStatus(str(value))
    except ValueError:
        return None


def build_execution_trace(
    *,
    run: Run,
    events: Iterable[AgentEvent],
    run_graph_id: str | None = None,
) -> ExecutionTrace:
    traversals: list[NodeTraversal] = []
    open_traversals: dict[str, int] = {}
    iterations: dict[str, LoopIterationTraceRef] = {}
    open_iteration: str | None = None
    runtime_calls: dict[str, RuntimeCallTraceRef] = {}
    tool_calls: dict[str, ToolCallTraceRef] = {}
    approvals: dict[str, ApprovalTraceRef] = {}
    verifications: list[VerificationTraceRef] = []
    checkpoints: list[CheckpointTraceRef] = []
    last_sequence = 0
    terminal_state: RunState | None = None

    for event in events:
        last_sequence = max(last_sequence, event.sequence)
        kind = event.normalized_type
        payload = event.payload

        if kind is EventType.NODE_ENTERED and event.node_id:
            open_traversals[event.node_id] = len(traversals)
            traversals.append(
                NodeTraversal(
                    node_id=event.node_id,
                    entered_sequence=event.sequence,
                    entered_at=event.timestamp,
                    status=GraphNodeStatus.RUNNING,
                    iteration_number=(
                        iterations[open_iteration].number if open_iteration else None
                    ),
                )
            )
        elif kind is EventType.NODE_EXITED and event.node_id:
            index = open_traversals.pop(event.node_id, None)
            if index is not None:
                traversals[index] = traversals[index].model_copy(
                    update={
                        "exited_sequence": event.sequence,
                        "exited_at": event.timestamp,
                        "status": _exit_status(payload.get("status")),
                    }
                )
        elif kind is EventType.LOOP_ITERATION_STARTED and payload.get("iteration_id"):
            iteration_id = str(payload["iteration_id"])
            open_iteration = iteration_id
            iterations[iteration_id] = LoopIterationTraceRef(
                iteration_id=iteration_id,
                number=int(payload.get("number", 0)),
                started_sequence=event.sequence,
            )
        elif kind is EventType.LOOP_ITERATION_COMPLETED and payload.get("iteration_id"):
            iteration_id = str(payload["iteration_id"])
            existing = iterations.get(iteration_id)
            if existing is None:
                existing = LoopIterationTraceRef(
                    iteration_id=iteration_id, number=int(payload.get("number", 0))
                )
            iterations[iteration_id] = existing.model_copy(
                update={
                    "completed_sequence": event.sequence,
                    "acceptance": _verification_status(payload.get("acceptance"))
                    or existing.acceptance,
                    "status": str(payload["status"]) if payload.get("status") else existing.status,
                }
            )
            if open_iteration == iteration_id:
                open_iteration = None
        elif kind is EventType.RUNTIME_CALL_STARTED and payload.get("runtime_call_id"):
            call_id = str(payload["runtime_call_id"])
            runtime_calls[call_id] = RuntimeCallTraceRef(
                runtime_call_id=call_id,
                node_id=event.node_id,
                started_sequence=event.sequence,
            )
        elif kind in _RUNTIME_CALL_TERMINALS and payload.get("runtime_call_id"):
            call_id = str(payload["runtime_call_id"])
            existing_call = runtime_calls.get(
                call_id, RuntimeCallTraceRef(runtime_call_id=call_id, node_id=event.node_id)
            )
            runtime_calls[call_id] = existing_call.model_copy(
                update={
                    "finished_sequence": event.sequence,
                    "outcome": _RUNTIME_CALL_TERMINALS[kind],
                }
            )
        elif kind in _TOOL_STATUS:
            key = tool_call_key(event)
            existing_tool = tool_calls.get(key)
            if existing_tool is None:
                tool_calls[key] = ToolCallTraceRef(
                    tool_call_id=key,
                    capability_id=(
                        str(payload["capability_id"]) if payload.get("capability_id") else None
                    ),
                    node_id=event.node_id,
                    requested_sequence=event.sequence,
                    status=_TOOL_STATUS[kind],
                )
            else:
                update: dict[str, object] = {"status": _TOOL_STATUS[kind]}
                if kind in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
                    update["finished_sequence"] = event.sequence
                tool_calls[key] = existing_tool.model_copy(update=update)
        elif kind is EventType.APPROVAL_REQUIRED and payload.get("approval_id"):
            approval_id = str(payload["approval_id"])
            approvals.setdefault(
                approval_id,
                ApprovalTraceRef(approval_id=approval_id, required_sequence=event.sequence),
            )
        elif kind is EventType.APPROVAL_RESOLVED and payload.get("approval_id"):
            approval_id = str(payload["approval_id"])
            existing_approval = approvals.get(
                approval_id,
                ApprovalTraceRef(approval_id=approval_id, required_sequence=event.sequence),
            )
            approvals[approval_id] = existing_approval.model_copy(
                update={"resolved_sequence": event.sequence, "resolved": True}
            )
        elif kind is EventType.VERIFICATION_RESULT and payload.get("verification_id"):
            verifications.append(
                VerificationTraceRef(
                    verification_id=str(payload["verification_id"]),
                    verifier_id=(
                        str(payload["verifier_id"]) if payload.get("verifier_id") else None
                    ),
                    iteration_id=(
                        str(payload["iteration_id"]) if payload.get("iteration_id") else None
                    ),
                    status=_verification_status(payload.get("status")),
                    sequence=event.sequence,
                )
            )
        elif kind is EventType.VERIFICATION_RESULT and payload.get("acceptance"):
            iteration_ref = payload.get("iteration_id")
            if iteration_ref and str(iteration_ref) in iterations:
                iteration_id = str(iteration_ref)
                iterations[iteration_id] = iterations[iteration_id].model_copy(
                    update={"acceptance": _verification_status(payload["acceptance"])}
                )
        elif kind is EventType.CHECKPOINT_SAVED and payload.get("checkpoint_id"):
            try:
                checkpoint_kind = CheckpointKind(str(payload.get("kind")))
            except ValueError:
                checkpoint_kind = CheckpointKind.NODE_BOUNDARY
            checkpoints.append(
                CheckpointTraceRef(
                    checkpoint_id=str(payload["checkpoint_id"]),
                    kind=checkpoint_kind,
                    node_id=event.node_id,
                    sequence=event.sequence,
                )
            )
        if kind in _TERMINAL_EVENT_STATES:
            terminal_state = _TERMINAL_EVENT_STATES[kind]

    if terminal_state is RunState.FAILED and run.state is RunState.REQUIRES_HUMAN:
        terminal_state = RunState.REQUIRES_HUMAN

    return ExecutionTrace(
        run_id=run.run_id,
        run_graph_id=run_graph_id,
        workflow_template_id=run.workflow_template_id or "unknown-template",
        traversals=traversals,
        loop_iterations=sorted(
            iterations.values(), key=lambda item: (item.number, item.iteration_id)
        ),
        runtime_calls=sorted(
            runtime_calls.values(),
            key=lambda item: (item.started_sequence or 0, item.runtime_call_id),
        ),
        tool_calls=sorted(
            tool_calls.values(), key=lambda item: (item.requested_sequence, item.tool_call_id)
        ),
        approvals=sorted(
            approvals.values(), key=lambda item: (item.required_sequence, item.approval_id)
        ),
        verifications=verifications,
        checkpoints=checkpoints,
        last_sequence=last_sequence,
        terminal_state=terminal_state,
    )
