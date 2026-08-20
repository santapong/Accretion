from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from accretion.contracts import (
    ContextBundle,
    ExecutionMode,
    ExpectedHorizon,
    FeatureEvidence,
    OverridePolicyResult,
    Project,
    PromptContract,
    RiskLevel,
    StrategyDecision,
    StrategyOverride,
    Task,
    TaskProfile,
    TaskType,
)
from accretion.ids import new_id

PROFILER_VERSION = "deterministic-profiler-v1"
SELECTOR_VERSION = "selector-v1"
PROMPT_VERSION = "p1-task-execution-v1"
CONTEXT_VERSION = "context-bundle-v1"
PROFILE_MIN_CONFIDENCE = 0.70

TEMPLATES = {
    ExecutionMode.DIRECT: "direct-v1",
    ExecutionMode.LOOP: "feedback-loop-v1",
    ExecutionMode.GRAPH: "fixed-graph-v1",
    ExecutionMode.HYBRID: "hybrid-rd-v1",
}
SAFE_UNKNOWN_TEMPLATE = "safe-unknown-v1"
REQUIRED_SELECTOR_FEATURES = (
    "complexity",
    "structure_certainty",
    "feedback_dependency",
    "dependency_complexity",
)
MANIFEST_NAMES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Makefile",
)
IRREVERSIBLE_CAPABILITY_MARKERS = (
    "delete",
    "deploy",
    "payment",
    "publish",
    "release",
    "send",
)


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 2)


def _repository_evidence(repository: Path) -> tuple[list[str], bool]:
    manifests = [name for name in MANIFEST_NAMES if (repository / name).is_file()]
    has_tests = any(
        candidate.exists()
        for candidate in (
            repository / "tests",
            repository / "test",
            repository / "spec",
            repository / "__tests__",
        )
    )
    if not has_tests:
        has_tests = any(repository.glob("*test*.*"))
    return manifests, has_tests


def _irreversible(capabilities: list[str]) -> bool:
    normalized = [
        capability.lower().replace("_", ".").replace("-", ".") for capability in capabilities
    ]
    return any(
        part in IRREVERSIBLE_CAPABILITY_MARKERS
        for capability in normalized
        for part in capability.split(".")
    )


def profile_task(task: Task, project: Project) -> TaskProfile:
    """Profile only typed task fields and repository facts; objective text is never parsed."""

    envelope = task.envelope
    manifests, has_tests = _repository_evidence(project.repository_path)
    base_scores: dict[TaskType, tuple[float, float, float]] = {
        TaskType.RESEARCH: (0.68, 0.45, 0.65),
        TaskType.ANALYSIS: (0.45, 0.75, 0.35),
        TaskType.IMPLEMENT: (0.55, 0.72, 0.50),
        TaskType.REVIEW: (0.20, 0.80, 0.20),
        TaskType.EXPERIMENT: (0.72, 0.60, 0.75),
    }
    base = base_scores.get(envelope.task_type)
    unknown_features: list[str] = []
    if base is None:
        complexity = structure = feedback = None
        unknown_features.extend(["complexity", "structure_certainty", "feedback_dependency"])
    else:
        output_weight = min(len(envelope.required_outputs), 4) * 0.04
        constraint_weight = min(len(envelope.constraints), 5) * 0.02
        complexity = _clamp(base[0] + output_weight + constraint_weight)
        structure = _clamp(base[1] + (0.05 if envelope.success_criteria else -0.05))
        feedback = _clamp(
            base[2] + (0.08 if has_tests and envelope.task_type is TaskType.IMPLEMENT else 0)
        )

    capability_count = len(envelope.allowed_capabilities) + len(envelope.requested_skills)
    dependency_complexity = _clamp(
        0.10 + min(capability_count, 6) * 0.09 + min(len(envelope.inputs), 4) * 0.05
    )
    parallelism_potential = _clamp(
        0.10
        + min(len(envelope.inputs), 4) * 0.12
        + min(envelope.budgets.max_parallel_runs - 1, 3) * 0.18
    )
    specificity = min(
        len(envelope.constraints)
        + len(envelope.success_criteria)
        + len(envelope.required_outputs)
        + len(envelope.inputs),
        8,
    )
    uncertainty = _clamp(0.75 - specificity * 0.07) if base is not None else None
    if uncertainty is None:
        unknown_features.append("uncertainty")
    verifier_strength = 0.90 if has_tests else (0.55 if envelope.success_criteria else 0.15)
    irreversible_actions = _irreversible(envelope.allowed_capabilities)
    if envelope.budgets.wall_time_seconds <= 900 and envelope.budgets.max_turns <= 10:
        horizon = ExpectedHorizon.SHORT
    elif envelope.budgets.wall_time_seconds > 3600 or envelope.budgets.max_turns > 30:
        horizon = ExpectedHorizon.LONG
    else:
        horizon = ExpectedHorizon.MEDIUM

    score_names = (
        "complexity",
        "structure_certainty",
        "feedback_dependency",
        "dependency_complexity",
        "parallelism_potential",
        "uncertainty",
        "verifier_strength",
    )
    confidence = _clamp((len(score_names) - len(unknown_features)) / len(score_names))
    evidence = [
        FeatureEvidence(
            feature="task_type",
            value=envelope.task_type.value,
            source="task-envelope",
            rationale="Typed task classification supplied by the operator.",
        ),
        FeatureEvidence(
            feature="repository_manifests",
            value=manifests,
            source="repository-scan",
            rationale="Recognized build and package manifests at repository root.",
        ),
        FeatureEvidence(
            feature="executable_tests",
            value=has_tests,
            source="repository-scan",
            rationale="Deterministic test-directory or root test-file check.",
        ),
        FeatureEvidence(
            feature="capability_dependencies",
            value=capability_count,
            source="task-envelope",
            rationale="Count of explicit skills and allowed capabilities.",
        ),
        FeatureEvidence(
            feature="irreversible_actions",
            value=irreversible_actions,
            source="task-envelope",
            rationale="Exact capability identifiers matched the irreversible capability registry.",
        ),
    ]
    evidence.extend(
        FeatureEvidence(
            feature=feature,
            value=None,
            source="typed-metadata",
            available=False,
            rationale="The supplied task type has no deterministic v1 score for this feature.",
        )
        for feature in unknown_features
    )
    return TaskProfile(
        profile_id=new_id("profile"),
        task_id=envelope.task_id,
        complexity=complexity,
        structure_certainty=structure,
        feedback_dependency=feedback,
        dependency_complexity=dependency_complexity,
        parallelism_potential=parallelism_potential,
        uncertainty=uncertainty,
        verifier_strength=verifier_strength,
        risk=envelope.risk_level,
        irreversible_actions=irreversible_actions,
        expected_horizon=horizon,
        profile_confidence=confidence,
        observed_features=evidence,
        unknown_features=unknown_features,
        semantic_rationale=(
            "Deterministic v1 profile derived from typed task metadata, budgets, capability "
            "dependencies, repository manifests, and test availability; objective text and LLM "
            "inference were not used."
        ),
    )


def select_strategy(profile: TaskProfile) -> StrategyDecision:
    high_risk = profile.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    required_unknowns = [
        feature for feature in REQUIRED_SELECTOR_FEATURES if getattr(profile, feature) is None
    ]
    if high_risk or profile.irreversible_actions:
        mode = ExecutionMode.GRAPH
        template = TEMPLATES[mode]
        rules = ["hard-policy:risk-or-irreversible"]
        rationale = "High-risk or irreversible work requires fixed approval and verifier gates."
    elif profile.profile_confidence < PROFILE_MIN_CONFIDENCE or required_unknowns:
        mode = ExecutionMode.HYBRID
        template = SAFE_UNKNOWN_TEMPLATE
        rules = ["fallback:low-confidence-or-required-unknown"]
        rationale = "Required deterministic evidence is unavailable; use the bounded safe fallback."
    elif (
        profile.complexity is not None
        and profile.feedback_dependency is not None
        and profile.dependency_complexity is not None
        and profile.complexity < 0.30
        and profile.feedback_dependency < 0.30
        and profile.dependency_complexity < 0.30
        and profile.risk is RiskLevel.LOW
    ):
        mode = ExecutionMode.DIRECT
        template = TEMPLATES[mode]
        rules = ["threshold:direct"]
        rationale = "Low complexity, feedback dependence, dependency complexity, and risk."
    elif (
        profile.structure_certainty is not None
        and profile.feedback_dependency is not None
        and profile.structure_certainty >= 0.70
        and profile.feedback_dependency < 0.45
    ):
        mode = ExecutionMode.GRAPH
        template = TEMPLATES[mode]
        rules = ["threshold:graph"]
        rationale = "Known macro-stages and low feedback dependence favor a fixed graph."
    elif (
        profile.feedback_dependency is not None
        and profile.dependency_complexity is not None
        and profile.feedback_dependency >= 0.60
        and profile.dependency_complexity < 0.45
    ):
        mode = ExecutionMode.LOOP
        template = TEMPLATES[mode]
        rules = ["threshold:loop"]
        rationale = "Feedback-dependent work with limited dependencies favors a bounded loop."
    else:
        mode = ExecutionMode.HYBRID
        template = TEMPLATES[mode]
        rules = ["threshold:hybrid-default"]
        rationale = "Mixed structure, feedback, and dependency signals favor the hybrid template."
    alternatives = [candidate for candidate in ExecutionMode if candidate is not mode]
    return StrategyDecision(
        decision_id=new_id("decision"),
        task_id=profile.task_id,
        selected_mode=mode,
        selected_template_id=template,
        task_profile_ref=profile.profile_id,
        matched_rules=rules,
        alternatives=alternatives,
        rationale=rationale,
        operator_override_allowed=True,
        requires_approval=high_risk or profile.irreversible_actions,
        requires_independent_verifier=high_risk or profile.irreversible_actions,
    )


def build_prompt_contract(task: Task) -> PromptContract:
    envelope = task.envelope
    tool_rules = [f"Allow capability: {item}" for item in envelope.allowed_capabilities]
    tool_rules.extend(f"Deny capability: {item}" for item in envelope.denied_capabilities)
    return PromptContract(
        prompt_contract_id=new_id("prompt"),
        task_id=envelope.task_id,
        role="execution-agent",
        objective=envelope.objective,
        hard_constraints=envelope.constraints,
        tool_rules=tool_rules,
        output_schema={"required_outputs": envelope.required_outputs},
        uncertainty_policy={"on_unknown": "surface-and-escalate", "fabrication": "forbidden"},
        completion_criteria=envelope.success_criteria,
    )


def build_context_bundle(task: Task, project: Project) -> ContextBundle:
    manifests, has_tests = _repository_evidence(project.repository_path)
    return ContextBundle(
        context_bundle_id=new_id("context"),
        task_ref=task.envelope.task_id,
        project_summary=f"{project.name} local repository",
        workspace_map={"root_manifests": manifests, "tests_available": has_tests},
        constraints=task.envelope.constraints,
        permissions=task.envelope.allowed_capabilities,
        freshness={"observed_at": datetime.now(UTC).isoformat(), "stale": False},
        provenance=["task-envelope", "project-record", "repository-scan"],
    )


def build_initial_planning(
    task: Task, project: Project
) -> tuple[PromptContract, ContextBundle, TaskProfile, StrategyDecision]:
    prompt = build_prompt_contract(task)
    context = build_context_bundle(task, project)
    profile = profile_task(task, project)
    decision = select_strategy(profile)
    return prompt, context, profile, decision


def evaluate_override(
    *,
    profile: TaskProfile,
    current: StrategyDecision,
    requested_mode: ExecutionMode,
    requested_template_id: str,
    reason: str,
    operator_identity: str,
) -> tuple[StrategyOverride, StrategyDecision | None]:
    expected_template = TEMPLATES[requested_mode]
    high_risk = profile.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    protected = high_risk or profile.irreversible_actions
    result = OverridePolicyResult.ACCEPTED
    denial: str | None = None
    if requested_template_id != expected_template:
        result = OverridePolicyResult.DENIED_TEMPLATE_MISMATCH
        denial = f"{requested_mode.value} requires template {expected_template}."
    elif protected and (
        requested_mode is not ExecutionMode.GRAPH or requested_template_id != "fixed-graph-v1"
    ):
        result = OverridePolicyResult.DENIED_SAFETY_POLICY
        denial = "High-risk or irreversible tasks must retain fixed approval and verifier gates."

    decision: StrategyDecision | None = None
    override_id = new_id("override")
    if result is OverridePolicyResult.ACCEPTED:
        decision = StrategyDecision(
            decision_id=new_id("decision"),
            task_id=profile.task_id,
            selected_mode=requested_mode,
            selected_template_id=requested_template_id,
            task_profile_ref=profile.profile_id,
            matched_rules=["operator-override", f"override:{override_id}"],
            alternatives=[
                candidate for candidate in ExecutionMode if candidate is not requested_mode
            ],
            rationale=f"Operator override accepted: {reason}",
            operator_override_allowed=True,
            requires_approval=protected,
            requires_independent_verifier=protected,
            supersedes_decision_id=current.decision_id,
        )
    override = StrategyOverride(
        override_id=override_id,
        task_id=profile.task_id,
        original_decision_id=current.decision_id,
        requested_mode=requested_mode,
        requested_template_id=requested_template_id,
        operator_identity=operator_identity,
        reason=reason,
        policy_result=result,
        accepted=decision is not None,
        resulting_decision_id=decision.decision_id if decision else None,
        denial_reason=denial,
    )
    return override, decision
