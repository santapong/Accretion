from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    ArtifactRef,
    ContextBundle,
    ErrorSummary,
    ExecutionMode,
    LoopExecution,
    LoopExecutionStatus,
    LoopIteration,
    LoopState,
    LoopStopReason,
    Project,
    PromptContract,
    Provider,
    Run,
    RunState,
    SessionRef,
    StrategyDecision,
    StrategyOverride,
    Task,
    TaskEnvelope,
    TaskPlanning,
    TaskProfile,
    VerificationResult,
    WorkspaceLease,
)
from accretion.persistence.models import (
    AcceptancePolicyRow,
    AgentEventRow,
    ContextBundleRow,
    LoopExecutionRow,
    LoopIterationRow,
    ProjectRow,
    PromptContractRow,
    RunRow,
    RuntimeSessionRow,
    StrategyDecisionRow,
    StrategyOverrideRow,
    TaskProfileRow,
    TaskRow,
    VerificationRow,
    WorkspaceLeaseRow,
)

_TERMINAL_LOOP_EXECUTION_STATUSES = {
    LoopExecutionStatus.SUCCEEDED,
    LoopExecutionStatus.FAILED,
    LoopExecutionStatus.CANCELLED,
    LoopExecutionStatus.REQUIRES_HUMAN,
}


class StateStore(Protocol):
    async def create_project(self, project: Project) -> Project: ...
    async def get_project(self, project_id: str) -> Project | None: ...
    async def list_projects(self) -> list[Project]: ...
    async def create_task(self, task: Task) -> Task: ...
    async def create_task_with_planning(
        self,
        task: Task,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> Task: ...
    async def get_task(self, task_id: str) -> Task | None: ...
    async def save_task_planning(
        self,
        task_id: str,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> TaskPlanning: ...
    async def get_task_planning(self, task_id: str) -> TaskPlanning | None: ...
    async def append_strategy_override(
        self, override: StrategyOverride, decision: StrategyDecision | None
    ) -> None: ...
    async def create_run(self, run: Run) -> Run: ...
    async def get_run(self, run_id: str) -> Run | None: ...
    async def list_runs(self, limit: int = 100) -> list[Run]: ...
    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        strategy_decision_id: str | None = None,
        execution_mode: ExecutionMode | None = None,
        workflow_template_id: str | None = None,
        acceptance_policy_id: str | None = None,
        loop_execution_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run: ...
    async def save_acceptance_policy(self, policy: AcceptancePolicy) -> None: ...
    async def get_acceptance_policy(self, policy_id: str) -> AcceptancePolicy | None: ...
    async def create_loop_execution(self, execution: LoopExecution) -> LoopExecution: ...
    async def get_loop_execution(self, loop_execution_id: str) -> LoopExecution | None: ...
    async def get_loop_execution_for_run(self, run_id: str) -> LoopExecution | None: ...
    async def update_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
    ) -> LoopExecution: ...
    async def append_loop_iteration(
        self,
        loop_execution_id: str,
        iteration: LoopIteration,
        next_state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
        verifications: Sequence[VerificationResult] = (),
        events: Sequence[AgentEvent] = (),
    ) -> LoopExecution: ...
    async def list_loop_iterations(self, loop_execution_id: str) -> list[LoopIteration]: ...
    async def save_verification(self, result: VerificationResult) -> None: ...
    async def get_verification(self, verification_id: str) -> VerificationResult | None: ...
    async def list_verifications(
        self, run_id: str, iteration_id: str | None = None
    ) -> list[VerificationResult]: ...
    async def save_lease(self, lease: WorkspaceLease) -> None: ...
    async def get_lease(self, lease_id: str) -> WorkspaceLease | None: ...
    async def save_session(self, session: SessionRef) -> None: ...
    async def get_session_for_run(self, run_id: str) -> SessionRef | None: ...
    async def save_artifact(self, artifact: ArtifactRef) -> None: ...
    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]: ...
    async def append_event(self, event: AgentEvent) -> AgentEvent: ...
    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]: ...


class MemoryStore:
    """Deterministic store for unit tests and protocol development, never production."""

    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, Run] = {}
        self.leases: dict[str, WorkspaceLease] = {}
        self.sessions: dict[str, SessionRef] = {}
        self.artifacts: dict[str, list[ArtifactRef]] = {}
        self.run_events: dict[str, list[AgentEvent]] = {}
        self.prompts: dict[str, PromptContract] = {}
        self.contexts: dict[str, ContextBundle] = {}
        self.profiles: dict[str, list[TaskProfile]] = {}
        self.decisions: dict[str, list[StrategyDecision]] = {}
        self.overrides: dict[str, list[StrategyOverride]] = {}
        self.acceptance_policies: dict[str, AcceptancePolicy] = {}
        self.loop_executions: dict[str, LoopExecution] = {}
        self.loop_execution_by_run: dict[str, str] = {}
        self.loop_iterations: dict[str, list[LoopIteration]] = {}
        self.verifications: dict[str, VerificationResult] = {}
        self.verification_ids_by_run: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def create_project(self, project: Project) -> Project:
        self.projects[project.project_id] = project
        return project

    async def get_project(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    async def list_projects(self) -> list[Project]:
        return sorted(self.projects.values(), key=lambda project: project.created_at)

    async def create_task(self, task: Task) -> Task:
        self.tasks[task.envelope.task_id] = task
        return task

    async def create_task_with_planning(
        self,
        task: Task,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> Task:
        async with self._lock:
            planned = task.model_copy(
                update={
                    "prompt_contract_id": prompt.prompt_contract_id,
                    "context_bundle_id": context.context_bundle_id,
                    "current_profile_id": profile.profile_id,
                    "current_strategy_decision_id": decision.decision_id,
                }
            )
            self.tasks[task.envelope.task_id] = planned
            self.prompts[prompt.prompt_contract_id] = prompt
            self.contexts[context.context_bundle_id] = context
            self.profiles[task.envelope.task_id] = [profile]
            self.decisions[task.envelope.task_id] = [decision]
            self.overrides[task.envelope.task_id] = []
            return planned

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def save_task_planning(
        self,
        task_id: str,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> TaskPlanning:
        async with self._lock:
            task = self.tasks[task_id]
            if task.current_strategy_decision_id is None:
                self.tasks[task_id] = task.model_copy(
                    update={
                        "prompt_contract_id": prompt.prompt_contract_id,
                        "context_bundle_id": context.context_bundle_id,
                        "current_profile_id": profile.profile_id,
                        "current_strategy_decision_id": decision.decision_id,
                    }
                )
                self.prompts[prompt.prompt_contract_id] = prompt
                self.contexts[context.context_bundle_id] = context
                self.profiles[task_id] = [profile]
                self.decisions[task_id] = [decision]
                self.overrides[task_id] = []
        planning = await self.get_task_planning(task_id)
        if planning is None:
            raise RuntimeError("planning records were not saved")
        return planning

    async def get_task_planning(self, task_id: str) -> TaskPlanning | None:
        task = self.tasks.get(task_id)
        if (
            task is None
            or task.prompt_contract_id is None
            or task.context_bundle_id is None
            or task.current_profile_id is None
            or task.current_strategy_decision_id is None
        ):
            return None
        profiles = self.profiles.get(task_id, [])
        decisions = self.decisions.get(task_id, [])
        current_profile = next(
            profile for profile in profiles if profile.profile_id == task.current_profile_id
        )
        current_decision = next(
            decision
            for decision in decisions
            if decision.decision_id == task.current_strategy_decision_id
        )
        return TaskPlanning(
            task_id=task_id,
            prompt_contract=self.prompts[task.prompt_contract_id],
            context_bundle=self.contexts[task.context_bundle_id],
            current_profile=current_profile,
            current_decision=current_decision,
            profile_history=profiles,
            decision_history=decisions,
            override_history=self.overrides.get(task_id, []),
        )

    async def append_strategy_override(
        self, override: StrategyOverride, decision: StrategyDecision | None
    ) -> None:
        async with self._lock:
            self.overrides.setdefault(override.task_id, []).append(override)
            if decision is not None:
                self.decisions.setdefault(override.task_id, []).append(decision)
                task = self.tasks[override.task_id]
                self.tasks[override.task_id] = task.model_copy(
                    update={"current_strategy_decision_id": decision.decision_id}
                )

    async def create_run(self, run: Run) -> Run:
        self.runs[run.run_id] = run
        return run

    async def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    async def list_runs(self, limit: int = 100) -> list[Run]:
        return sorted(self.runs.values(), key=lambda run: run.created_at, reverse=True)[:limit]

    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        strategy_decision_id: str | None = None,
        execution_mode: ExecutionMode | None = None,
        workflow_template_id: str | None = None,
        acceptance_policy_id: str | None = None,
        loop_execution_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        current = self.runs[run_id]
        updated = current.model_copy(
            update={
                "state": state,
                "session_id": session_id if session_id is not None else current.session_id,
                "workspace_lease_id": (
                    workspace_lease_id
                    if workspace_lease_id is not None
                    else current.workspace_lease_id
                ),
                "strategy_decision_id": (
                    strategy_decision_id
                    if strategy_decision_id is not None
                    else current.strategy_decision_id
                ),
                "execution_mode": (
                    execution_mode if execution_mode is not None else current.execution_mode
                ),
                "workflow_template_id": (
                    workflow_template_id
                    if workflow_template_id is not None
                    else current.workflow_template_id
                ),
                "acceptance_policy_id": (
                    acceptance_policy_id
                    if acceptance_policy_id is not None
                    else current.acceptance_policy_id
                ),
                "loop_execution_id": (
                    loop_execution_id
                    if loop_execution_id is not None
                    else current.loop_execution_id
                ),
                "error": error,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.runs[run_id] = updated
        return updated

    async def save_acceptance_policy(self, policy: AcceptancePolicy) -> None:
        async with self._lock:
            current = self.acceptance_policies.get(policy.policy_id)
            if current is not None and current != policy:
                raise ValueError(f"acceptance policy {policy.policy_id} is immutable")
            self.acceptance_policies[policy.policy_id] = policy

    async def get_acceptance_policy(self, policy_id: str) -> AcceptancePolicy | None:
        return self.acceptance_policies.get(policy_id)

    async def create_loop_execution(self, execution: LoopExecution) -> LoopExecution:
        async with self._lock:
            if execution.acceptance_policy_ref not in self.acceptance_policies:
                raise KeyError(execution.acceptance_policy_ref)
            if execution.run_id not in self.runs:
                raise KeyError(execution.run_id)
            if execution.loop_execution_id in self.loop_executions:
                raise ValueError(f"loop execution {execution.loop_execution_id} already exists")
            if execution.run_id in self.loop_execution_by_run:
                raise ValueError(f"run {execution.run_id} already has a loop execution")
            policy = self.acceptance_policies[execution.acceptance_policy_ref]
            stored = execution.model_copy(update={"acceptance_policy": policy})
            self.loop_executions[stored.loop_execution_id] = stored
            self.loop_execution_by_run[stored.run_id] = stored.loop_execution_id
            self.loop_iterations[stored.loop_execution_id] = []
            run = self.runs[stored.run_id]
            self.runs[stored.run_id] = run.model_copy(
                update={
                    "acceptance_policy_id": stored.acceptance_policy_ref,
                    "loop_execution_id": stored.loop_execution_id,
                    "revision": run.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            return stored

    async def get_loop_execution(self, loop_execution_id: str) -> LoopExecution | None:
        return self.loop_executions.get(loop_execution_id)

    async def get_loop_execution_for_run(self, run_id: str) -> LoopExecution | None:
        loop_execution_id = self.loop_execution_by_run.get(run_id)
        return self.loop_executions.get(loop_execution_id) if loop_execution_id else None

    async def update_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
    ) -> LoopExecution:
        async with self._lock:
            return self._update_memory_loop_execution(
                loop_execution_id,
                state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=expected_revision,
            )

    async def append_loop_iteration(
        self,
        loop_execution_id: str,
        iteration: LoopIteration,
        next_state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
        verifications: Sequence[VerificationResult] = (),
        events: Sequence[AgentEvent] = (),
    ) -> LoopExecution:
        async with self._lock:
            current = self.loop_executions[loop_execution_id]
            self._validate_iteration_transition(current, iteration, next_state)
            if expected_revision is not None and current.revision != expected_revision:
                raise ValueError("loop execution revision conflict")
            self._validate_iteration_evidence(iteration, verifications, events)
            if any(
                stored.iteration_id == iteration.iteration_id
                for iterations in self.loop_iterations.values()
                for stored in iterations
            ):
                raise ValueError("iteration identifier already exists")
            if any(result.verification_id in self.verifications for result in verifications):
                raise ValueError("verification identifier already exists")
            existing_event_ids = {
                stored.event_id for stored in self.run_events.get(iteration.run_id, [])
            }
            new_event_ids = {event.event_id for event in events}
            if len(new_event_ids) != len(events) or existing_event_ids.intersection(new_event_ids):
                raise ValueError("event identifier already exists")
            self.loop_iterations[loop_execution_id].append(iteration)
            for result in verifications:
                self.verifications[result.verification_id] = result
                self.verification_ids_by_run.setdefault(result.run_id, []).append(
                    result.verification_id
                )
            run_events = self.run_events.setdefault(iteration.run_id, [])
            run = self.runs[iteration.run_id]
            for event in events:
                stored_event = event.model_copy(update={"sequence": len(run_events) + 1})
                run_events.append(stored_event)
            if events:
                self.runs[iteration.run_id] = run.model_copy(
                    update={"last_sequence": run_events[-1].sequence}
                )
            return self._update_memory_loop_execution(
                loop_execution_id,
                next_state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=current.revision,
            )

    async def list_loop_iterations(self, loop_execution_id: str) -> list[LoopIteration]:
        return list(self.loop_iterations.get(loop_execution_id, []))

    async def save_verification(self, result: VerificationResult) -> None:
        async with self._lock:
            current = self.verifications.get(result.verification_id)
            if current is not None:
                if current != result:
                    raise ValueError(f"verification {result.verification_id} is immutable")
                return
            if result.run_id not in self.runs:
                raise KeyError(result.run_id)
            if result.iteration_id is not None and not any(
                item.iteration_id == result.iteration_id
                for iterations in self.loop_iterations.values()
                for item in iterations
            ):
                raise ValueError(
                    "iteration verification must be saved with append_loop_iteration"
                )
            self.verifications[result.verification_id] = result
            self.verification_ids_by_run.setdefault(result.run_id, []).append(
                result.verification_id
            )

    async def get_verification(self, verification_id: str) -> VerificationResult | None:
        return self.verifications.get(verification_id)

    async def list_verifications(
        self, run_id: str, iteration_id: str | None = None
    ) -> list[VerificationResult]:
        results = [
            self.verifications[verification_id]
            for verification_id in self.verification_ids_by_run.get(run_id, [])
        ]
        if iteration_id is not None:
            results = [result for result in results if result.iteration_id == iteration_id]
        return sorted(results, key=lambda result: (result.executed_at, result.verification_id))

    def _update_memory_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None,
        stop_reason: LoopStopReason | None,
        expected_revision: int | None,
    ) -> LoopExecution:
        current = self.loop_executions[loop_execution_id]
        if current.status in _TERMINAL_LOOP_EXECUTION_STATUSES:
            raise ValueError("terminal loop execution is immutable")
        if expected_revision is not None and current.revision != expected_revision:
            raise ValueError("loop execution revision conflict")
        next_status = status or current.status
        now = datetime.now(UTC)
        completed_at = (
            now if next_status in _TERMINAL_LOOP_EXECUTION_STATUSES else current.completed_at
        )
        updated = current.model_copy(
            update={
                "state": state,
                "status": next_status,
                "stop_reason": stop_reason,
                "revision": current.revision + 1,
                "updated_at": now,
                "completed_at": completed_at,
            }
        )
        self.loop_executions[loop_execution_id] = updated
        return updated

    @staticmethod
    def _validate_iteration_transition(
        execution: LoopExecution, iteration: LoopIteration, next_state: LoopState
    ) -> None:
        if execution.status in _TERMINAL_LOOP_EXECUTION_STATUSES:
            raise ValueError("terminal loop execution cannot accept iterations")
        if iteration.loop_execution_id != execution.loop_execution_id:
            raise ValueError("iteration belongs to a different loop execution")
        if iteration.run_id != execution.run_id:
            raise ValueError("iteration belongs to a different run")
        expected_number = execution.state.iteration + 1
        if iteration.number != expected_number or next_state.iteration != expected_number:
            raise ValueError(f"expected loop iteration {expected_number}")

    @staticmethod
    def _validate_iteration_evidence(
        iteration: LoopIteration,
        verifications: Sequence[VerificationResult],
        events: Sequence[AgentEvent],
    ) -> None:
        verification_ids = {result.verification_id for result in verifications}
        if len(verification_ids) != len(verifications):
            raise ValueError("verification identifiers must be unique")
        if set(iteration.verification_refs) != verification_ids:
            raise ValueError("iteration verification refs must match persisted verifications")
        if any(
            result.run_id != iteration.run_id
            or result.iteration_id != iteration.iteration_id
            for result in verifications
        ):
            raise ValueError("verification belongs to a different run or iteration")
        if any(event.run_id != iteration.run_id for event in events):
            raise ValueError("event belongs to a different run")

    async def save_lease(self, lease: WorkspaceLease) -> None:
        self.leases[lease.lease_id] = lease

    async def get_lease(self, lease_id: str) -> WorkspaceLease | None:
        return self.leases.get(lease_id)

    async def save_session(self, session: SessionRef) -> None:
        self.sessions[session.run_id] = session

    async def get_session_for_run(self, run_id: str) -> SessionRef | None:
        return self.sessions.get(run_id)

    async def save_artifact(self, artifact: ArtifactRef) -> None:
        self.artifacts.setdefault(artifact.run_id, []).append(artifact)

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        return self.artifacts.get(run_id, [])

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self._lock:
            events = self.run_events.setdefault(event.run_id, [])
            stored = event.model_copy(update={"sequence": len(events) + 1})
            events.append(stored)
            run = self.runs.get(event.run_id)
            if run is not None:
                self.runs[event.run_id] = run.model_copy(update={"last_sequence": stored.sequence})
            return stored

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        return [event for event in self.run_events.get(run_id, []) if event.sequence > after]


class PostgresStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create_project(self, project: Project) -> Project:
        async with self.sessions.begin() as session:
            session.add(
                ProjectRow(
                    id=project.project_id,
                    name=project.name,
                    repository_path=str(project.repository_path),
                    created_at=project.created_at,
                )
            )
        return project

    async def get_project(self, project_id: str) -> Project | None:
        async with self.sessions() as session:
            row = await session.get(ProjectRow, project_id)
        if row is None:
            return None
        return Project(
            project_id=row.id,
            name=row.name,
            repository_path=row.repository_path,
            created_at=row.created_at,
        )

    async def list_projects(self) -> list[Project]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(ProjectRow).order_by(ProjectRow.created_at))).all()
        return [
            Project(
                project_id=row.id,
                name=row.name,
                repository_path=row.repository_path,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def create_task(self, task: Task) -> Task:
        async with self.sessions.begin() as session:
            session.add(
                TaskRow(
                    id=task.envelope.task_id,
                    project_id=task.envelope.project_id,
                    envelope=task.envelope.model_dump(mode="json"),
                    prompt_contract_id=task.prompt_contract_id,
                    context_bundle_id=task.context_bundle_id,
                    current_profile_id=task.current_profile_id,
                    current_strategy_decision_id=task.current_strategy_decision_id,
                    created_at=task.created_at,
                )
            )
        return task

    async def create_task_with_planning(
        self,
        task: Task,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> Task:
        planned = task.model_copy(
            update={
                "prompt_contract_id": prompt.prompt_contract_id,
                "context_bundle_id": context.context_bundle_id,
                "current_profile_id": profile.profile_id,
                "current_strategy_decision_id": decision.decision_id,
            }
        )
        async with self.sessions.begin() as session:
            session.add(self._task_to_row(planned))
            await session.flush()
            await self._add_planning_rows(session, prompt, context, profile, decision)
        return planned

    async def get_task(self, task_id: str) -> Task | None:
        async with self.sessions() as session:
            row = await session.get(TaskRow, task_id)
        if row is None:
            return None
        return self._row_to_task(row)

    async def save_task_planning(
        self,
        task_id: str,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> TaskPlanning:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(TaskRow).where(TaskRow.id == task_id).with_for_update()
            )
            if row is None:
                raise KeyError(task_id)
            if row.current_strategy_decision_id is None:
                await self._add_planning_rows(session, prompt, context, profile, decision)
                row.prompt_contract_id = prompt.prompt_contract_id
                row.context_bundle_id = context.context_bundle_id
                row.current_profile_id = profile.profile_id
                row.current_strategy_decision_id = decision.decision_id
        planning = await self.get_task_planning(task_id)
        if planning is None:
            raise RuntimeError("planning records were not saved")
        return planning

    async def get_task_planning(self, task_id: str) -> TaskPlanning | None:
        async with self.sessions() as session:
            task = await session.get(TaskRow, task_id)
            if task is None:
                raise KeyError(task_id)
            if (
                task.prompt_contract_id is None
                or task.context_bundle_id is None
                or task.current_profile_id is None
                or task.current_strategy_decision_id is None
            ):
                return None
            prompt = await session.get(PromptContractRow, task.prompt_contract_id)
            context = await session.get(ContextBundleRow, task.context_bundle_id)
            current_profile = await session.get(TaskProfileRow, task.current_profile_id)
            current_decision = await session.get(
                StrategyDecisionRow, task.current_strategy_decision_id
            )
            profile_rows = (
                await session.scalars(
                    select(TaskProfileRow)
                    .where(TaskProfileRow.task_id == task_id)
                    .order_by(TaskProfileRow.created_at, TaskProfileRow.id)
                )
            ).all()
            decision_rows = (
                await session.scalars(
                    select(StrategyDecisionRow)
                    .where(StrategyDecisionRow.task_id == task_id)
                    .order_by(StrategyDecisionRow.created_at, StrategyDecisionRow.id)
                )
            ).all()
            override_rows = (
                await session.scalars(
                    select(StrategyOverrideRow)
                    .where(StrategyOverrideRow.task_id == task_id)
                    .order_by(StrategyOverrideRow.created_at, StrategyOverrideRow.id)
                )
            ).all()
        if prompt is None or context is None or current_profile is None or current_decision is None:
            raise RuntimeError(f"task {task_id} has incomplete planning references")
        return TaskPlanning(
            task_id=task_id,
            prompt_contract=PromptContract.model_validate(prompt.contract),
            context_bundle=ContextBundle.model_validate(context.bundle),
            current_profile=TaskProfile.model_validate(current_profile.profile),
            current_decision=StrategyDecision.model_validate(current_decision.decision),
            profile_history=[TaskProfile.model_validate(row.profile) for row in profile_rows],
            decision_history=[
                StrategyDecision.model_validate(row.decision) for row in decision_rows
            ],
            override_history=[
                StrategyOverride.model_validate(row.override) for row in override_rows
            ],
        )

    async def append_strategy_override(
        self, override: StrategyOverride, decision: StrategyDecision | None
    ) -> None:
        async with self.sessions.begin() as session:
            task = await session.scalar(
                select(TaskRow).where(TaskRow.id == override.task_id).with_for_update()
            )
            if task is None:
                raise KeyError(override.task_id)
            if task.current_strategy_decision_id != override.original_decision_id:
                raise ValueError("strategy decision changed before the override could be recorded")
            if decision is not None:
                session.add(self._decision_to_row(decision))
                await session.flush()
                task.current_strategy_decision_id = decision.decision_id
            session.add(
                StrategyOverrideRow(
                    id=override.override_id,
                    task_id=override.task_id,
                    original_decision_id=override.original_decision_id,
                    resulting_decision_id=override.resulting_decision_id,
                    operator_identity=override.operator_identity,
                    accepted=override.accepted,
                    override=override.model_dump(mode="json"),
                    created_at=override.created_at,
                )
            )

    async def create_run(self, run: Run) -> Run:
        async with self.sessions.begin() as session:
            session.add(self._run_to_row(run))
        return run

    async def get_run(self, run_id: str) -> Run | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
        return self._row_to_run(row) if row else None

    async def list_runs(self, limit: int = 100) -> list[Run]:
        async with self.sessions() as session:
            rows: Sequence[RunRow] = (
                await session.scalars(
                    select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
                )
            ).all()
        return [self._row_to_run(row) for row in rows]

    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        strategy_decision_id: str | None = None,
        execution_mode: ExecutionMode | None = None,
        workflow_template_id: str | None = None,
        acceptance_policy_id: str | None = None,
        loop_execution_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
            if row is None:
                raise KeyError(run_id)
            row.state = state.value
            row.session_id = session_id if session_id is not None else row.session_id
            row.workspace_lease_id = (
                workspace_lease_id if workspace_lease_id is not None else row.workspace_lease_id
            )
            row.strategy_decision_id = (
                strategy_decision_id
                if strategy_decision_id is not None
                else row.strategy_decision_id
            )
            row.execution_mode = (
                execution_mode.value if execution_mode is not None else row.execution_mode
            )
            row.workflow_template_id = (
                workflow_template_id
                if workflow_template_id is not None
                else row.workflow_template_id
            )
            row.acceptance_policy_id = (
                acceptance_policy_id
                if acceptance_policy_id is not None
                else row.acceptance_policy_id
            )
            row.loop_execution_id = (
                loop_execution_id if loop_execution_id is not None else row.loop_execution_id
            )
            row.error = error.model_dump(mode="json") if error else None
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            await session.flush()
            result = self._row_to_run(row)
        return result

    async def save_acceptance_policy(self, policy: AcceptancePolicy) -> None:
        async with self.sessions.begin() as session:
            current = await session.get(AcceptancePolicyRow, policy.policy_id)
            if current is not None:
                if AcceptancePolicy.model_validate(current.policy) != policy:
                    raise ValueError(f"acceptance policy {policy.policy_id} is immutable")
                return
            session.add(
                AcceptancePolicyRow(
                    id=policy.policy_id,
                    version=policy.version,
                    policy=policy.model_dump(mode="json"),
                    created_at=policy.created_at,
                )
            )

    async def get_acceptance_policy(self, policy_id: str) -> AcceptancePolicy | None:
        async with self.sessions() as session:
            row = await session.get(AcceptancePolicyRow, policy_id)
        return AcceptancePolicy.model_validate(row.policy) if row else None

    async def create_loop_execution(self, execution: LoopExecution) -> LoopExecution:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == execution.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(execution.run_id)
            if run.loop_execution_id is not None:
                raise ValueError(f"run {execution.run_id} already has a loop execution")
            policy = await session.get(AcceptancePolicyRow, execution.acceptance_policy_ref)
            if policy is None:
                raise KeyError(execution.acceptance_policy_ref)
            stored_policy = AcceptancePolicy.model_validate(policy.policy)
            session.add(self._loop_execution_to_row(execution))
            run.acceptance_policy_id = execution.acceptance_policy_ref
            run.loop_execution_id = execution.loop_execution_id
            run.revision += 1
            run.updated_at = datetime.now(UTC)
        return execution.model_copy(
            update={"acceptance_policy": stored_policy}
        )

    async def get_loop_execution(self, loop_execution_id: str) -> LoopExecution | None:
        async with self.sessions() as session:
            row = await session.get(LoopExecutionRow, loop_execution_id)
            if row is None:
                return None
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
        return self._row_to_loop_execution(row, policy)

    async def get_loop_execution_for_run(self, run_id: str) -> LoopExecution | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(LoopExecutionRow).where(LoopExecutionRow.run_id == run_id)
            )
            if row is None:
                return None
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
        return self._row_to_loop_execution(row, policy)

    async def update_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
    ) -> LoopExecution:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(LoopExecutionRow)
                .where(LoopExecutionRow.id == loop_execution_id)
                .with_for_update()
            )
            if row is None:
                raise KeyError(loop_execution_id)
            self._update_loop_row(
                row,
                state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=expected_revision,
            )
            await session.flush()
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
            updated = self._row_to_loop_execution(row, policy)
        return updated

    async def append_loop_iteration(
        self,
        loop_execution_id: str,
        iteration: LoopIteration,
        next_state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
        verifications: Sequence[VerificationResult] = (),
        events: Sequence[AgentEvent] = (),
    ) -> LoopExecution:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == iteration.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(iteration.run_id)
            row = await session.scalar(
                select(LoopExecutionRow)
                .where(LoopExecutionRow.id == loop_execution_id)
                .with_for_update()
            )
            if row is None:
                raise KeyError(loop_execution_id)
            current = self._row_to_loop_execution(row)
            MemoryStore._validate_iteration_transition(current, iteration, next_state)
            if expected_revision is not None and row.revision != expected_revision:
                raise ValueError("loop execution revision conflict")
            MemoryStore._validate_iteration_evidence(iteration, verifications, events)
            session.add(
                LoopIterationRow(
                    id=iteration.iteration_id,
                    loop_execution_id=iteration.loop_execution_id,
                    run_id=iteration.run_id,
                    number=iteration.number,
                    iteration=iteration.model_dump(mode="json"),
                    created_at=iteration.completed_at,
                )
            )
            for result in verifications:
                session.add(
                    VerificationRow(
                        id=result.verification_id,
                        run_id=result.run_id,
                        loop_execution_id=loop_execution_id,
                        iteration_id=result.iteration_id,
                        verifier_id=result.verifier_id,
                        verifier_version=result.verifier_version,
                        target_ref=result.target_ref,
                        status=result.status.value,
                        result=result.model_dump(mode="json"),
                        executed_at=result.executed_at,
                    )
                )
            for event in events:
                run.last_sequence += 1
                stored_event = event.model_copy(update={"sequence": run.last_sequence})
                session.add(self._event_to_row(stored_event))
            self._update_loop_row(
                row,
                next_state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=row.revision,
            )
            await session.flush()
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
            updated = self._row_to_loop_execution(row, policy)
        return updated

    async def list_loop_iterations(self, loop_execution_id: str) -> list[LoopIteration]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(LoopIterationRow)
                    .where(LoopIterationRow.loop_execution_id == loop_execution_id)
                    .order_by(LoopIterationRow.number)
                )
            ).all()
        return [LoopIteration.model_validate(row.iteration) for row in rows]

    async def save_verification(self, result: VerificationResult) -> None:
        async with self.sessions.begin() as session:
            current = await session.get(VerificationRow, result.verification_id)
            if current is not None:
                if VerificationResult.model_validate(current.result) != result:
                    raise ValueError(f"verification {result.verification_id} is immutable")
                return
            loop_execution_id: str | None = None
            if result.iteration_id is not None:
                iteration = await session.get(LoopIterationRow, result.iteration_id)
                if iteration is None:
                    raise ValueError(
                        "iteration verification must be saved with append_loop_iteration"
                    )
                loop_execution_id = iteration.loop_execution_id
            session.add(
                VerificationRow(
                    id=result.verification_id,
                    run_id=result.run_id,
                    loop_execution_id=loop_execution_id,
                    iteration_id=result.iteration_id,
                    verifier_id=result.verifier_id,
                    verifier_version=result.verifier_version,
                    target_ref=result.target_ref,
                    status=result.status.value,
                    result=result.model_dump(mode="json"),
                    executed_at=result.executed_at,
                )
            )

    async def get_verification(self, verification_id: str) -> VerificationResult | None:
        async with self.sessions() as session:
            row = await session.get(VerificationRow, verification_id)
        return VerificationResult.model_validate(row.result) if row else None

    async def list_verifications(
        self, run_id: str, iteration_id: str | None = None
    ) -> list[VerificationResult]:
        query = select(VerificationRow).where(VerificationRow.run_id == run_id)
        if iteration_id is not None:
            query = query.where(VerificationRow.iteration_id == iteration_id)
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    query.order_by(VerificationRow.executed_at, VerificationRow.id)
                )
            ).all()
        return [VerificationResult.model_validate(row.result) for row in rows]

    async def save_lease(self, lease: WorkspaceLease) -> None:
        async with self.sessions.begin() as session:
            session.add(
                WorkspaceLeaseRow(
                    id=lease.lease_id,
                    project_id=lease.project_id,
                    run_id=lease.run_id,
                    base_revision=lease.base_revision,
                    path=str(lease.path),
                    branch_name=lease.branch_name,
                    cleanup_policy=lease.cleanup_policy,
                    acquired_at=lease.acquired_at,
                    expires_at=lease.expires_at,
                )
            )

    async def get_lease(self, lease_id: str) -> WorkspaceLease | None:
        async with self.sessions() as session:
            row = await session.get(WorkspaceLeaseRow, lease_id)
        if row is None:
            return None
        return WorkspaceLease(
            lease_id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            base_revision=row.base_revision,
            path=row.path,
            branch_name=row.branch_name,
            cleanup_policy=row.cleanup_policy,
            acquired_at=row.acquired_at,
            expires_at=row.expires_at,
        )

    async def save_session(self, runtime_session: SessionRef) -> None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(RuntimeSessionRow)
                .where(RuntimeSessionRow.run_id == runtime_session.run_id)
                .with_for_update()
            )
            if row is None:
                session.add(
                    RuntimeSessionRow(
                        id=runtime_session.session_id,
                        run_id=runtime_session.run_id,
                        provider=runtime_session.provider.value,
                        native_session_id=runtime_session.native_session_id,
                        workspace_path=str(runtime_session.workspace),
                        created_at=datetime.now(UTC),
                    )
                )
            else:
                row.id = runtime_session.session_id
                row.provider = runtime_session.provider.value
                row.native_session_id = runtime_session.native_session_id
                row.workspace_path = str(runtime_session.workspace)

    async def get_session_for_run(self, run_id: str) -> SessionRef | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(RuntimeSessionRow).where(RuntimeSessionRow.run_id == run_id)
            )
        if row is None:
            return None
        return SessionRef(
            session_id=row.id,
            run_id=row.run_id,
            provider=Provider(row.provider),
            native_session_id=row.native_session_id,
            workspace=row.workspace_path,
        )

    async def save_artifact(self, artifact: ArtifactRef) -> None:
        from accretion.persistence.models import ArtifactRow

        async with self.sessions.begin() as session:
            session.add(
                ArtifactRow(
                    id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    kind=artifact.kind,
                    path=str(artifact.path),
                    sha256=artifact.sha256,
                    created_at=datetime.now(UTC),
                )
            )

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        from accretion.persistence.models import ArtifactRow

        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ArtifactRow)
                    .where(ArtifactRow.run_id == run_id)
                    .order_by(ArtifactRow.created_at)
                )
            ).all()
        return [
            ArtifactRef(
                artifact_id=row.id,
                run_id=row.run_id,
                kind=row.kind,
                path=row.path,
                sha256=row.sha256,
            )
            for row in rows
        ]

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == event.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(event.run_id)
            run.last_sequence += 1
            sequence = run.last_sequence
            stored = event.model_copy(update={"sequence": sequence})
            session.add(
                AgentEventRow(
                    id=stored.event_id,
                    run_id=stored.run_id,
                    session_id=stored.session_id,
                    provider=stored.provider.value,
                    native_type=stored.native_type,
                    normalized_type=stored.normalized_type.value,
                    sequence=sequence,
                    occurred_at=stored.timestamp,
                    correlation_id=stored.correlation_id,
                    causation_id=stored.causation_id,
                    node_id=stored.node_id,
                    payload=stored.payload,
                    adapter_version=stored.adapter_version,
                )
            )
        return stored

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentEventRow)
                    .where(AgentEventRow.run_id == run_id, AgentEventRow.sequence > after)
                    .order_by(AgentEventRow.sequence)
                )
            ).all()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _task_to_row(task: Task) -> TaskRow:
        return TaskRow(
            id=task.envelope.task_id,
            project_id=task.envelope.project_id,
            envelope=task.envelope.model_dump(mode="json"),
            prompt_contract_id=task.prompt_contract_id,
            context_bundle_id=task.context_bundle_id,
            current_profile_id=task.current_profile_id,
            current_strategy_decision_id=task.current_strategy_decision_id,
            created_at=task.created_at,
        )

    @staticmethod
    def _row_to_task(row: TaskRow) -> Task:
        return Task(
            envelope=TaskEnvelope.model_validate(row.envelope),
            prompt_contract_id=row.prompt_contract_id,
            context_bundle_id=row.context_bundle_id,
            current_profile_id=row.current_profile_id,
            current_strategy_decision_id=row.current_strategy_decision_id,
            created_at=row.created_at,
        )

    @classmethod
    async def _add_planning_rows(
        cls,
        session: AsyncSession,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> None:
        session.add_all(
            [
                PromptContractRow(
                    id=prompt.prompt_contract_id,
                    task_id=prompt.task_id,
                    version=prompt.version,
                    contract=prompt.model_dump(mode="json"),
                    created_at=prompt.created_at,
                ),
                ContextBundleRow(
                    id=context.context_bundle_id,
                    task_id=context.task_ref,
                    version=context.version,
                    bundle=context.model_dump(mode="json"),
                    created_at=context.created_at,
                ),
                TaskProfileRow(
                    id=profile.profile_id,
                    task_id=profile.task_id,
                    profiler_version=profile.profiler_version,
                    profile=profile.model_dump(mode="json"),
                    created_at=profile.created_at,
                ),
            ]
        )
        await session.flush()
        session.add(cls._decision_to_row(decision))

    @staticmethod
    def _decision_to_row(decision: StrategyDecision) -> StrategyDecisionRow:
        return StrategyDecisionRow(
            id=decision.decision_id,
            task_id=decision.task_id,
            task_profile_id=decision.task_profile_ref,
            policy_version=decision.policy_version,
            decision=decision.model_dump(mode="json"),
            created_at=decision.created_at,
        )

    @staticmethod
    def _run_to_row(run: Run) -> RunRow:
        return RunRow(
            id=run.run_id,
            task_id=run.task_id,
            project_id=run.project_id,
            provider=run.provider.value,
            state=run.state.value,
            last_sequence=run.last_sequence,
            revision=run.revision,
            session_id=run.session_id,
            workspace_lease_id=run.workspace_lease_id,
            strategy_decision_id=run.strategy_decision_id,
            execution_mode=run.execution_mode.value if run.execution_mode else None,
            workflow_template_id=run.workflow_template_id,
            acceptance_policy_id=run.acceptance_policy_id,
            loop_execution_id=run.loop_execution_id,
            error=run.error.model_dump(mode="json") if run.error else None,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _row_to_run(row: RunRow) -> Run:
        return Run(
            run_id=row.id,
            task_id=row.task_id,
            project_id=row.project_id,
            provider=Provider(row.provider),
            state=RunState(row.state),
            last_sequence=row.last_sequence,
            revision=row.revision,
            session_id=row.session_id,
            workspace_lease_id=row.workspace_lease_id,
            strategy_decision_id=row.strategy_decision_id,
            execution_mode=ExecutionMode(row.execution_mode) if row.execution_mode else None,
            workflow_template_id=row.workflow_template_id,
            acceptance_policy_id=row.acceptance_policy_id,
            loop_execution_id=row.loop_execution_id,
            error=ErrorSummary.model_validate(row.error) if row.error else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _loop_execution_to_row(execution: LoopExecution) -> LoopExecutionRow:
        return LoopExecutionRow(
            id=execution.loop_execution_id,
            run_id=execution.run_id,
            acceptance_policy_id=execution.acceptance_policy_ref,
            spec=execution.spec.model_dump(mode="json"),
            state=execution.state.model_dump(mode="json"),
            status=execution.status.value,
            stop_reason=execution.stop_reason.value if execution.stop_reason else None,
            revision=execution.revision,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            completed_at=execution.completed_at,
        )

    @staticmethod
    def _row_to_loop_execution(
        row: LoopExecutionRow, policy: AcceptancePolicyRow | None = None
    ) -> LoopExecution:
        acceptance_policy = AcceptancePolicy.model_validate(policy.policy) if policy else None
        return LoopExecution(
            loop_execution_id=row.id,
            run_id=row.run_id,
            spec=row.spec,
            state=row.state,
            acceptance_policy_ref=row.acceptance_policy_id,
            acceptance_policy=acceptance_policy,
            status=row.status,
            stop_reason=row.stop_reason,
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _update_loop_row(
        row: LoopExecutionRow,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None,
        stop_reason: LoopStopReason | None,
        expected_revision: int | None,
    ) -> None:
        if LoopExecutionStatus(row.status) in _TERMINAL_LOOP_EXECUTION_STATUSES:
            raise ValueError("terminal loop execution is immutable")
        if expected_revision is not None and row.revision != expected_revision:
            raise ValueError("loop execution revision conflict")
        if status is not None:
            row.status = status.value
        row.state = state.model_dump(mode="json")
        row.stop_reason = stop_reason.value if stop_reason else None
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        if LoopExecutionStatus(row.status) in _TERMINAL_LOOP_EXECUTION_STATUSES:
            row.completed_at = row.updated_at

    @staticmethod
    def _row_to_event(row: AgentEventRow) -> AgentEvent:
        return AgentEvent(
            event_id=row.id,
            run_id=row.run_id,
            session_id=row.session_id,
            provider=Provider(row.provider),
            native_type=row.native_type,
            normalized_type=row.normalized_type,
            sequence=row.sequence,
            timestamp=row.occurred_at,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            node_id=row.node_id,
            payload=row.payload,
            adapter_version=row.adapter_version,
        )

    @staticmethod
    def _event_to_row(event: AgentEvent) -> AgentEventRow:
        return AgentEventRow(
            id=event.event_id,
            run_id=event.run_id,
            session_id=event.session_id,
            provider=event.provider.value,
            native_type=event.native_type,
            normalized_type=event.normalized_type.value,
            sequence=event.sequence,
            occurred_at=event.timestamp,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            node_id=event.node_id,
            payload=event.payload,
            adapter_version=event.adapter_version,
        )
