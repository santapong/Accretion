"""Freeze graph-node requirements before deterministic routing.

The order in this module is an authority boundary, not an implementation detail.  A
verification specification is persisted before the node contract that pins it; only after
both immutable records exist may candidate construction begin.  All record identities and
timestamps are stable, and an existing stored record is returned verbatim on replay.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from accretion.contracts import (
    AcceptancePolicy,
    GraphNodeKind,
    PrincipalRef,
    RiskLevel,
    Run,
    RunNode,
    Task,
    WorkflowNodeSpec,
    WorkflowTemplate,
)
from accretion.contracts.canonical import canonical_json
from accretion.contracts.refs import ApprovalArtifactRef, CapabilityRef, PolicyRef
from accretion.contracts.routing import (
    CapabilityRequirement,
    NodeContract,
    ObjectiveContract,
    ObjectiveContractRef,
    ResourceBudget,
    RiskClass,
    UtilityWeights,
    VerificationSpecRef,
)
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.identity import VerificationSpecBuilder, execution_instance_id
from accretion.routing.protocols import FrozenNode

VERIFIED_SUCCESS_FLOOR = 0.5
FALSE_ACCEPTANCE_CEILING = 0.5
UTILITY_PROFILE_ID = "quality-cost-latency/1"

_RISK_CLASS_BY_LEVEL: dict[RiskLevel, RiskClass] = {
    RiskLevel.LOW: RiskClass.LOW_DIGITAL,
    RiskLevel.MEDIUM: RiskClass.MEDIUM_DIGITAL,
    RiskLevel.HIGH: RiskClass.HIGH_DIGITAL,
    # The v0.4 routing vocabulary has no generic CRITICAL digital class.  Mapping to its
    # only CRITICAL-equivalent preserves (and never weakens) the task's authority ceiling.
    RiskLevel.CRITICAL: RiskClass.PHYSICAL_HIGH,
}


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _policy_ref(policy: AcceptancePolicy) -> PolicyRef:
    return PolicyRef(
        policy_id=policy.policy_id,
        version=policy.version,
        content_digest=_digest(policy.model_dump(mode="json")),
    )


class ObjectiveContractMinter:
    """Mint and persist the one objective contract authorised by a task."""

    def __init__(
        self, *, store: StateStore, created_by: PrincipalRef, workspace_id: str
    ) -> None:
        self.store = store
        self.created_by = created_by
        self.workspace_id = workspace_id

    async def for_task(
        self, task: Task, policy: AcceptancePolicy
    ) -> ObjectiveContractRef:
        """Return a stable reference to the task's persisted objective contract.

        A task is the approval artifact available at this boundary: its immutable envelope
        says what may be done, under which risk and budget.  The content-addressed
        ``accretion://`` reference records that fact without inventing an external receipt.
        """

        contract_id = derived_id("objective_contract", task.envelope.task_id)
        objective = await self.store.get_objective_contract(contract_id)
        risk_policy = _policy_ref(policy)
        if objective is None:
            task_digest = _digest(task.model_dump(mode="json"))
            objective = ObjectiveContract(  # type: ignore[call-arg]
                contract_id=contract_id,
                created_at=task.created_at,
                created_by=self.created_by,
                workspace_id=self.workspace_id,
                project_id=task.envelope.project_id,
                goal=task.envelope.objective,
                scope_in=[f"task:{task.envelope.task_id}"],
                scope_out=[
                    f"capability:{capability_id}"
                    for capability_id in sorted(set(task.envelope.denied_capabilities))
                ],
                verified_success_floor=VERIFIED_SUCCESS_FLOOR,
                false_acceptance_ceiling=FALSE_ACCEPTANCE_CEILING,
                utility_weights=UtilityWeights(quality=1.0, cost=0.25, latency=0.15),
                risk_policy_ref=risk_policy,
                resource_budget=ResourceBudget(
                    maximum_cost=Decimal("0"),
                    maximum_latency_ms=task.envelope.budgets.wall_time_seconds * 1_000,
                    maximum_attempts=task.envelope.budgets.max_loop_iterations,
                    maximum_tool_calls=task.envelope.budgets.max_tool_calls,
                ),
                revision=1,
                approval_receipt_ref=ApprovalArtifactRef(
                    uri=f"accretion://tasks/{task.envelope.task_id}",
                    digest=task_digest,
                    media_type="application/json",
                    retention_class="AUDIT",
                ),
            )
            objective = await self.store.put_objective_contract(objective)

        return ObjectiveContractRef(  # type: ignore[call-arg]
            contract_id=f"{objective.contract_id}:ref",
            created_at=objective.created_at,
            created_by=objective.created_by,
            workspace_id=objective.workspace_id,
            project_id=objective.project_id,
            objective_contract_id=objective.contract_id,
            revision=objective.revision,
            objective_contract_hash=objective.content_hash,
            verified_success_floor=objective.verified_success_floor,
            utility_profile_id=UTILITY_PROFILE_ID,
            risk_policy=objective.risk_policy_ref,
            approved_by=objective.created_by,
            approved_at=objective.created_at,
        )


class NodeContractFreezer:
    """Persist one verification spec and node contract for one execution attempt."""

    def __init__(
        self, *, store: StateStore, created_by: PrincipalRef, workspace_id: str
    ) -> None:
        self.store = store
        self.created_by = created_by
        self.workspace_id = workspace_id
        self.objectives = ObjectiveContractMinter(
            store=store, created_by=created_by, workspace_id=workspace_id
        )
        self.verification_specs = VerificationSpecBuilder(
            created_by=created_by, workspace_id=workspace_id
        )

    async def freeze(
        self,
        *,
        run: Run,
        task: Task,
        node: RunNode,
        spec: WorkflowNodeSpec,
        template: WorkflowTemplate,
        policy: AcceptancePolicy,
        graph_revision: int,
        attempt: int,
    ) -> FrozenNode:
        """Freeze exactly the kwargs declared by ``NodeRoutingService.freeze``."""

        if node.kind not in {
            GraphNodeKind.AGENT,
            GraphNodeKind.TOOL,
            GraphNodeKind.VERIFIER,
        }:
            raise ValueError(f"{node.kind.value} nodes are not routable")
        if node.key != spec.key or node.kind is not spec.kind:
            raise ValueError(
                f"run node {node.key!r}/{node.kind.value} does not match template node "
                f"{spec.key!r}/{spec.kind.value}"
            )
        graph = await self.store.get_run_graph(run.run_id)
        if graph is None:
            raise ValueError(f"run {run.run_id!r} has no graph to freeze")
        if graph.run_graph_id == "":
            raise ValueError(f"run {run.run_id!r} has an empty graph identity")

        objective_ref = await self.objectives.for_task(task, policy)
        verifier_ids = list(
            dict.fromkeys([*policy.required_verifiers, *template.required_verifiers])
        )
        effective_policy = policy.model_copy(update={"required_verifiers": verifier_ids})
        verification_spec = self.verification_specs.build(task, effective_policy)

        # ADR-044: persist the verifier's success semantics before anything can pin them.
        stored_spec = await self.store.get_verification_spec(verification_spec.contract_id)
        if stored_spec is None:
            stored_spec = await self.store.put_verification_spec(verification_spec)

        execution_id = execution_instance_id(run.run_id, node.key, attempt)
        requirements = await self._capability_requirements(spec, task)
        earlier = [
            contract
            for contract in await self.store.list_node_contracts(
                workspace_id=self.workspace_id, project_id=task.envelope.project_id
            )
            if contract.run_graph_id == graph.run_graph_id
            and contract.node_id == node.node_id
            and contract.graph_revision < graph_revision
        ]
        supersedes = (
            max(earlier, key=lambda contract: contract.graph_revision).contract_id
            if earlier
            else None
        )
        contract_id = derived_id(
            "node_contract",
            graph.run_graph_id,
            str(graph_revision),
            node.node_id,
            execution_id,
            objective_ref.objective_contract_hash,
            stored_spec.content_hash,
        )
        node_contract = await self.store.get_node_contract(contract_id)
        if node_contract is None:
            node_contract = NodeContract(  # type: ignore[call-arg]
                contract_id=contract_id,
                created_at=task.created_at,
                created_by=self.created_by,
                workspace_id=self.workspace_id,
                project_id=task.envelope.project_id,
                supersedes_contract_id=supersedes,
                objective_contract_ref=objective_ref,
                labels={
                    "run_id": run.run_id,
                    "node_key": node.key,
                    "attempt": str(attempt),
                },
                node_id=node.node_id,
                run_graph_id=graph.run_graph_id,
                graph_revision=graph_revision,
                execution_instance_id=execution_id,
                objective=spec.instruction or task.envelope.objective,
                node_kind=node.kind,
                required_capabilities=requirements,
                allowed_risk_class=_RISK_CLASS_BY_LEVEL[task.envelope.risk_level],
                resource_cap=ResourceBudget(
                    maximum_cost=Decimal("0"),
                    maximum_latency_ms=min(
                        task.envelope.budgets.wall_time_seconds,
                        template.global_budget_policy.max_wall_time_seconds,
                    )
                    * 1_000,
                    maximum_attempts=min(
                        task.envelope.budgets.max_loop_iterations,
                        template.global_budget_policy.max_node_retries + 1,
                    ),
                    maximum_tool_calls=min(
                        task.envelope.budgets.max_tool_calls,
                        template.global_budget_policy.max_total_tool_calls,
                    ),
                ),
                verification_spec_ref=VerificationSpecRef(
                    verification_spec_id=stored_spec.contract_id,
                    content_hash=stored_spec.content_hash,
                ),
                failure_policy_ref=_policy_ref(policy),
            )
            node_contract = await self.store.put_node_contract(node_contract)

        return FrozenNode(
            node_contract=node_contract,
            verification_spec=stored_spec,
            objective_ref=objective_ref,
            execution_instance_id=execution_id,
        )

    async def _capability_requirements(
        self, spec: WorkflowNodeSpec, task: Task
    ) -> list[CapabilityRequirement]:
        requirements: list[CapabilityRequirement] = []
        capability_ids = sorted(
            set(spec.capability_refs) | set(task.envelope.allowed_capabilities)
        )
        for capability_id in capability_ids:
            capability = await self.store.get_capability(capability_id)
            version = capability.version if capability is not None else "unknown"
            required_scope = (
                ",".join(sorted(set(capability.required_permissions)))
                if capability is not None and capability.required_permissions
                else "execute"
            )
            requirements.append(
                CapabilityRequirement(
                    capability=CapabilityRef(
                        capability_id=capability_id, capability_version=version
                    ),
                    version_range=f"=={version}" if capability is not None else "*",
                    required_scope=required_scope,
                )
            )
        return requirements


__all__ = [
    "FALSE_ACCEPTANCE_CEILING",
    "NodeContractFreezer",
    "ObjectiveContractMinter",
    "UTILITY_PROFILE_ID",
    "VERIFIED_SUCCESS_FLOOR",
]
