"""Bounded construction of complete, policy-gated M2 configuration candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import islice, product

from accretion.contracts import Capability, PrincipalRef, Task
from accretion.contracts.routing import (
    CompatibilityDecision,
    CompatibilityStatus,
    ConfigurationCandidate,
    ConstructionStage,
    ExecutionConfiguration,
    NodeContract,
    RejectedCandidate,
    VerifierBinding,
)
from accretion.ids import derived_id
from accretion.routing.catalog import (
    BEAM_WIDTH_BY_NODE_CLASS,
    ConfigurationCatalog,
    ToolCatalogEntry,
)
from accretion.routing.gates import JointEvaluator, PolicyGate, gate_then_evaluate
from accretion.routing.selector import cold_start_predictions
from accretion.routing.snapshot import RoutingSnapshot


@dataclass(frozen=True, slots=True)
class CandidateBuildResult:
    """The eligible slate, explicit refusals, and every gate/rule decision."""

    candidates: tuple[ConfigurationCandidate, ...]
    rejected: tuple[RejectedCandidate, ...]
    compatibility_decisions: tuple[CompatibilityDecision, ...]


class CandidateBuilder:
    """Construct complete tuples only from content-bound catalog and snapshot entries."""

    def __init__(
        self,
        *,
        gate: PolicyGate,
        evaluator: JointEvaluator,
        catalog: ConfigurationCatalog,
        created_by: PrincipalRef,
    ) -> None:
        self.gate = gate
        self.evaluator = evaluator
        self.catalog = catalog
        self.created_by = created_by

    def build(
        self,
        *,
        routing_request_id: str,
        node_contract: NodeContract,
        task: Task,
        principal: PrincipalRef,
        entitled_workspace_id: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> CandidateBuildResult:
        """Run stages 2--8; authority and compatibility always precede candidate scoring."""

        at = clock() if clock is not None else node_contract.created_at
        if node_contract.workspace_id != workspace_id or node_contract.project_id != project_id:
            raise ValueError("node contract scope does not match candidate-build scope")
        if task.envelope.project_id != project_id:
            raise ValueError("task scope does not match candidate-build scope")

        rejected: list[RejectedCandidate] = []
        decisions: list[CompatibilityDecision] = []
        tool_choices: list[tuple[ToolCatalogEntry, ...]] = []
        missing_requirements: list[str] = []
        for requirement in node_contract.required_capabilities:
            capability_id = requirement.capability.capability_id
            options = tuple(
                entry
                for entry in self.catalog.tools
                if entry.binding.capability.capability_id == capability_id
                and entry.binding.capability.capability_version
                == requirement.capability.capability_version
                and (
                    not entry.connection_required
                    or requirement.required_scope in entry.granted_scopes
                )
                and self._tool_is_in_snapshot(entry, snapshot)
            )
            if not options:
                missing_requirements.append(capability_id)
            tool_choices.append(options)

        requested_skills = tuple(dict.fromkeys(task.envelope.requested_skills))
        skill_choices = [
            tuple(
                skill
                for skill in self.catalog.skills
                if skill.skill_id == skill_id and skill_id in snapshot.skills
            )
            for skill_id in requested_skills
        ]
        missing_requirements.extend(
            skill_id
            for skill_id, options in zip(requested_skills, skill_choices, strict=True)
            if not options
        )
        if missing_requirements:
            for missing in sorted(set(missing_requirements)):
                rejected.append(
                    RejectedCandidate(
                        candidate_id=derived_id(
                            "configuration_candidate", routing_request_id, "missing", missing
                        ),
                        stage=ConstructionStage.RESOLVE_REQUIREMENTS,
                        reason_code="REQUIREMENT_UNAVAILABLE",
                        detail=f"Required registry entry {missing!r} was unavailable.",
                    )
                )
            return CandidateBuildResult((), tuple(rejected), ())

        cap = BEAM_WIDTH_BY_NODE_CLASS.get(node_contract.node_kind.value)
        if cap is None:
            raise ValueError(f"node kind {node_contract.node_kind.value} is not routable")
        runtime_models = tuple(
            option
            for option in self.catalog.runtime_models
            if self._runtime_is_in_snapshot(
                option.runtime.runtime_id, option.runtime.adapter_version, snapshot
            )
        )[:cap]
        verifiers = tuple(
            entry
            for entry in self.catalog.verifiers
            if entry.verifier.verifier_contract_id in snapshot.verifier_ids
        )[: BEAM_WIDTH_BY_NODE_CLASS["VERIFIER"]]
        environments = self.catalog.environments[:cap]
        if not runtime_models or not verifiers or not environments:
            missing = (
                "RUNTIME_MODEL_UNAVAILABLE"
                if not runtime_models
                else "VERIFIER_UNAVAILABLE"
                if not verifiers
                else "ENVIRONMENT_UNAVAILABLE"
            )
            rejected.append(
                RejectedCandidate(
                    candidate_id=derived_id(
                        "configuration_candidate", routing_request_id, "missing", missing
                    ),
                    stage=ConstructionStage.RESOLVE_REQUIREMENTS,
                    reason_code=missing,
                    detail=f"No exact catalog entry passed {missing} checks.",
                )
            )
            return CandidateBuildResult((), tuple(rejected), ())

        tool_products = tuple(islice(product(*tool_choices), cap)) if tool_choices else ((),)
        skill_products = tuple(islice(product(*skill_choices), cap)) if skill_choices else ((),)
        configurations: list[ExecutionConfiguration] = []
        complete_products = product(
            environments, runtime_models, tool_products, skill_products, verifiers
        )
        for environment, runtime_model, tools, skills, verifier in islice(
            complete_products, cap
        ):
            semantic_key = (
                environment.environment.environment_id,
                runtime_model.runtime.runtime_id,
                runtime_model.runtime.adapter_version,
                runtime_model.model.model_id,
                *(item.binding.binding_id for item in tools),
                *(item.skill_id for item in skills),
                verifier.verifier.verifier_contract_id,
                node_contract.verification_spec_ref.content_hash,
            )
            configurations.append(
                ExecutionConfiguration(  # type: ignore[call-arg]
                    contract_id=derived_id(
                        "execution_configuration",
                        self.catalog.digest,
                        *(str(item) for item in semantic_key),
                    ),
                    created_at=at,
                    created_by=self.created_by,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    objective_contract_ref=node_contract.objective_contract_ref,
                    labels={"catalog_digest": self.catalog.digest},
                    environment=environment,
                    runtime=runtime_model.runtime,
                    model=runtime_model.model,
                    tools=[item.binding for item in tools],
                    skills=list(skills),
                    verifier=VerifierBinding(
                        verifier=verifier.verifier,
                        version=verifier.version,
                        verification_spec_hash=node_contract.verification_spec_ref.content_hash,
                    ),
                )
            )

        # An audited fallback is an explicit complete configuration, not a reconstruction
        # from whichever partial entries happened to survive a beam.
        configurations.extend(self.catalog.fallback_bundle.configurations)
        deduplicated = {
            item.configuration_hash: item
            for item in sorted(
                configurations,
                key=lambda value: (value.configuration_hash, value.contract_id),
            )
        }
        fallback_hashes = (
            self.catalog.fallback_bundle.configuration_hashes
            if snapshot.fallback_bundle_digest == self.catalog.fallback_bundle.digest
            else frozenset()
        )

        eligible_candidates: list[ConfigurationCandidate] = []
        for configuration in deduplicated.values():
            requirement_failure = self._requirement_failure(configuration, node_contract, task)
            candidate_id = derived_id(
                "configuration_candidate", routing_request_id, configuration.configuration_hash
            )
            if requirement_failure is not None:
                rejected.append(
                    RejectedCandidate(
                        candidate_id=candidate_id,
                        stage=ConstructionStage.RESOLVE_REQUIREMENTS,
                        reason_code=requirement_failure,
                        detail=(
                            "Complete configuration does not satisfy every frozen registry "
                            "requirement."
                        ),
                    )
                )
                continue
            capabilities = self._configuration_capabilities(configuration, snapshot)
            evaluation = gate_then_evaluate(
                gate=self.gate,
                evaluator=self.evaluator,
                task=task,
                principal=principal,
                entitled_workspace_id=entitled_workspace_id,
                capabilities=capabilities,
                node_contract=node_contract,
                configuration=configuration,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=lambda: at,
            )
            decisions.extend(evaluation.decisions())
            if not evaluation.eligible():
                refusal = next(
                    decision
                    for decision in evaluation.decisions()
                    if decision.status is not CompatibilityStatus.COMPATIBLE
                )
                rejected.append(
                    RejectedCandidate(
                        candidate_id=candidate_id,
                        stage=ConstructionStage.JOINT_COMPATIBILITY,
                        reason_code=refusal.reason_code,
                        detail=f"Configuration was rejected by rule {refusal.rule_id}.",
                    )
                )
                continue
            predictions = cold_start_predictions()
            eligible_candidates.append(
                ConfigurationCandidate(  # type: ignore[call-arg]
                    contract_id=candidate_id,
                    created_at=at,
                    created_by=self.created_by,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    objective_contract_ref=node_contract.objective_contract_ref,
                    labels={"catalog_digest": self.catalog.digest},
                    routing_request_id=routing_request_id,
                    configuration=configuration,
                    construction_stage=ConstructionStage.PREDICT_OUTCOME,
                    hard_eligible=True,
                    compatibility_decision_refs=[
                        decision.contract_id for decision in evaluation.decisions()
                    ],
                    predicted=predictions,
                    uncertainty_score=0.5,
                    lower_confidence_success=predictions.node_verified_success.lower_bound,
                    fallback_eligible=configuration.configuration_hash in fallback_hashes,
                )
            )

        eligible_candidates.sort(
            key=lambda item: (
                not item.fallback_eligible,
                item.configuration.configuration_hash,
                item.contract_id,
            )
        )
        kept = eligible_candidates[:cap]
        kept_ids = {item.contract_id for item in kept}
        compatible_fallback = next(
            (item for item in eligible_candidates if item.fallback_eligible), None
        )
        if compatible_fallback is not None and compatible_fallback.contract_id not in kept_ids:
            kept = (
                [*kept[:-1], compatible_fallback]
                if kept
                else [compatible_fallback]
            )
            kept_ids = {item.contract_id for item in kept}
        for item in eligible_candidates:
            if item.contract_id not in kept_ids:
                rejected.append(
                    RejectedCandidate(
                        candidate_id=item.contract_id,
                        stage=ConstructionStage.PREDICT_OUTCOME,
                        reason_code="BEAM_PRUNED",
                        detail="Candidate was outside the bounded node-class beam.",
                    )
                )
        return CandidateBuildResult(tuple(kept), tuple(rejected), tuple(decisions))

    @staticmethod
    def _runtime_is_in_snapshot(
        runtime_id: str, adapter_version: str, snapshot: RoutingSnapshot
    ) -> bool:
        health = snapshot.runtime(runtime_id)
        return health is not None and health.runtime_version == adapter_version

    @staticmethod
    def _tool_is_in_snapshot(entry: ToolCatalogEntry, snapshot: RoutingSnapshot) -> bool:
        resolved = snapshot.resolved(entry.binding.capability.capability_id)
        return (
            resolved is not None
            and resolved.binding is not None
            and resolved.binding.enabled
            and resolved.binding.binding_id == entry.binding.binding_id
            and resolved.capability.version == entry.binding.capability.capability_version
        )

    @staticmethod
    def _configuration_capabilities(
        configuration: ExecutionConfiguration, snapshot: RoutingSnapshot
    ) -> list[Capability]:
        capabilities: list[Capability] = []
        for tool in configuration.tools:
            resolved = snapshot.resolved(tool.capability.capability_id)
            if resolved is not None:
                capabilities.append(resolved.capability)
        return capabilities

    def _requirement_failure(
        self, configuration: ExecutionConfiguration, node: NodeContract, task: Task
    ) -> str | None:
        """Check the requirement-to-binding facts the M1 snapshot intentionally omits."""

        configured_skills = {item.skill_id for item in configuration.skills}
        if any(skill_id not in configured_skills for skill_id in task.envelope.requested_skills):
            return "REQUIREMENT_UNAVAILABLE"
        tools = {
            (item.capability.capability_id, item.capability.capability_version): item
            for item in configuration.tools
        }
        for requirement in node.required_capabilities:
            key = (
                requirement.capability.capability_id,
                requirement.capability.capability_version,
            )
            tool = tools.get(key)
            if tool is None:
                return "REQUIREMENT_UNAVAILABLE"
            entry = next(
                (
                    item
                    for item in self.catalog.tools
                    if item.binding.binding_id == tool.binding_id
                    and item.binding.capability == tool.capability
                ),
                None,
            )
            if entry is None:
                return "REQUIREMENT_UNAVAILABLE"
            if entry.connection_required and requirement.required_scope not in entry.granted_scopes:
                return "SCOPE_INSUFFICIENT"
        return None


__all__ = ["CandidateBuildResult", "CandidateBuilder"]
