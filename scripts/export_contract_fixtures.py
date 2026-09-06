"""Regenerate the committed golden fixtures for the v0.4 contract family (registry §19).

Registry §19 requires that "golden fixtures cover minimal, complete, invalid, and
unknown-version cases" for every contract. This script writes those four files for each of
the twenty-one models in :data:`~accretion.contracts.routing.CONTRACT_INVENTORY`, under
``tests/fixtures/contracts/v0.4/<model_snake_case>/``. The files are committed; the tests in
``tests/test_v04_m0_fixtures.py`` read them and never regenerate them, so a fixture edited
by hand — or a model changed without regenerating — is a red test rather than a silent drift.

The four kinds, and what each one is for:

``minimal.json``
    Only the fields the model cannot be constructed without, plus ``created_at`` and
    ``content_hash``. Those two are pinned rather than defaulted because ``created_at``
    would otherwise be "now" and the fixture's digest would change on every run; pinning
    them is what makes a *golden* fixture golden. Everything else is absent, so this file
    is also the proof that every optional field really is optional.

``complete.json``
    Every field of the model, all of them non-null, with real derived digests. This is the
    file the hash checks run against: the committed JSON hashes — through
    :func:`~accretion.contracts.canonical.content_hash`, over the raw parsed JSON — to
    exactly the ``content_hash`` recorded inside it, which is only true because the
    canonical form of a decimal-as-string and an RFC 3339 ``Z`` timestamp is the same
    whether it arrives as JSON text or as a parsed Python object.

``invalid.json``
    The complete document with **one** named violation applied, and the expected error
    substring recorded under ``_expect``. The ``content_hash`` is dropped: an invalid
    document is not a sealed one, and leaving a stale digest would have made every invalid
    fixture fail for the same uninteresting reason instead of for the violation it names.

``unknown_version.json``
    The complete document with ``schema_version`` set to ``2.0.0`` and nothing else changed.
    It must be rejected by the major-version check, which is a *field* validator precisely
    so that it fires before the body rules and before the now-stale digest.

Usage::

    uv run --no-sync python scripts/export_contract_fixtures.py

Regenerating is a deliberate act. A schema change that moves these files is a versioned
migration under registry §17, not a refresh.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    CONTRACT_INVENTORY,
    ExecutionConfiguration,
    ObjectiveContractRef,
    ProjectFeatures,
    StructuredExplanation,
    TaskFeatures,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "contracts" / "v0.4"

FIXTURE_KINDS: tuple[str, ...] = ("minimal", "complete", "invalid", "unknown_version")
"""The four registry §19 cases, in the order they are written and parametrized."""

UNKNOWN_MAJOR_VERSION = "2.0.0"
"""The version ``unknown_version.json`` declares. Any unknown major would do; this is the next."""


def snake_case(name: str) -> str:
    """``RouterModelVersion`` → ``router_model_version``; the fixture directory name."""

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(label: str) -> str:
    """A stable, obviously-synthetic sha256 for a fixture. Same label, same digest, forever."""

    return hashlib.sha256(f"accretion-v0.4-fixture:{label}".encode()).hexdigest()


def identifier(prefix: str, seed: str) -> str:
    """A deterministic id in the repository's ``prefix_<26 base32 chars>`` shape (ADR-055).

    ``accretion.ids.new_id`` mints from the clock and ``os.urandom``, which is exactly what
    a golden fixture must not do. The body here is derived from the label so that two
    fixtures never accidentally share an id, and it is drawn from the same base32 alphabet
    so that ``has_prefix`` and every human reading a fixture see the same shape.
    """

    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    raw = hashlib.sha256(f"{prefix}:{seed}".encode()).digest()
    body = "".join(alphabet[byte % 32] for byte in raw[:26])
    return f"{prefix}_{body}"


# --------------------------------------------------------------------------------------
# Shared fragments. Every fixture is built from these, so a reader comparing two fixtures
# sees the difference between the contracts rather than the difference between two invented
# principals.
# --------------------------------------------------------------------------------------

WORKSPACE_ID = identifier("wks", "freeze")
PROJECT_ID = identifier("prj", "freeze")
CREATED_AT = "2026-03-01T09:00:00Z"

PRINCIPAL: dict[str, Any] = {
    "principal_id": identifier("usr", "freeze"),
    "display_name": "v0.4 contract freeze fixture",
    "status": "ACTIVE",
}
POLICY_REF: dict[str, Any] = {
    "policy_id": "workspace-risk-policy",
    "version": "3.1.0",
    "content_digest": digest("risk-policy"),
}
FAILURE_POLICY_REF: dict[str, Any] = {
    "policy_id": "workspace-failure-policy",
    "version": "1.4.0",
    "content_digest": digest("failure-policy"),
}
APPROVAL_ARTIFACT_REF: dict[str, Any] = {
    "uri": "https://approvals.example.test/receipts/2026-02-01",
    "digest": digest("approval-receipt"),
    "media_type": "application/pdf",
    "retention_class": "STANDARD",
}
CAPABILITY_REF: dict[str, Any] = {"capability_id": "fs.read", "capability_version": "1.2.0"}
TOOL_REF: dict[str, Any] = {"tool_id": "ripgrep", "implementation_digest": digest("tool")}
SKILL_REF: dict[str, Any] = {
    "skill_id": "code-review",
    "version": "2.0.0",
    "package_digest": digest("skill"),
}
ENVIRONMENT_REF: dict[str, Any] = {
    "environment_id": "sandboxed-worktree",
    "image_digest": digest("image"),
    "policy_profile": "restricted-egress",
}
VERIFIER_REF: dict[str, Any] = {
    "verifier_contract_id": "diff-and-suite",
    "implementation_digest": digest("verifier"),
}
RUNTIME_REF: dict[str, Any] = {
    "runtime_id": "claude-cli",
    "adapter_version": "accretion-claude-v1",
    "provider": "CLAUDE",
    "model": "claude-opus-4",
    "capability_profile_digest": digest("runtime-profile"),
}
EVIDENCE_REF: dict[str, Any] = {
    "evidence_id": identifier("evd", "deterministic"),
    "evidence_class": "DIGITAL",
    "content_digest": digest("evidence"),
}
SIMULATION_EVIDENCE_REF: dict[str, Any] = {
    "evidence_id": identifier("evd", "simulated"),
    "evidence_class": "SIMULATION",
    "content_digest": digest("simulated-evidence"),
}

VERIFICATION_SPEC_HASH = digest("verification-spec")
NODE_CONTRACT_IMMUTABLE_HASH = digest("node-contract")
CONFIGURATION_HASH = digest("execution-configuration")

RESOURCE_BUDGET: dict[str, Any] = {
    "maximum_cost": "12.50",
    "maximum_latency_ms": 900_000,
    "maximum_attempts": 3,
    "maximum_tool_calls": 200,
}
UTILITY_WEIGHTS: dict[str, Any] = {"quality": 0.6, "cost": 0.25, "latency": 0.15}
PERMISSION_PROVENANCE: dict[str, Any] = {
    "scope": "TEAM_WORKSPACE",
    "policy": POLICY_REF,
    "granted_by": PRINCIPAL,
    "justification": "Shared under the workspace evidence-sharing policy.",
}


def estimate(mean: float, spread: float, method: str) -> dict[str, Any]:
    """One :class:`~accretion.contracts.routing.DistributionEstimate` around ``mean``."""

    return {
        "mean": mean,
        "lower_bound": round(mean - spread, 6),
        "upper_bound": round(mean + spread, 6),
        "confidence": 0.9,
        "method": method,
    }


PREDICTED_OUTCOMES: dict[str, Any] = {
    "quality": estimate(0.82, 0.06, "bootstrap-1000"),
    "cost": estimate(4.5, 1.25, "bootstrap-1000"),
    "latency": estimate(41_000.0, 9_000.0, "bootstrap-1000"),
    "node_verified_success": estimate(0.88, 0.07, "conformal-v1"),
    "run_verified_success": estimate(0.71, 0.12, "conformal-v1"),
}


def comparison(metric_id: str, delta: float, passed: bool) -> dict[str, Any]:
    """One :class:`~accretion.contracts.routing.MetricComparison`, bounds bracketing ``delta``."""

    return {
        "metric_id": metric_id,
        "baseline_value": 0.70,
        "candidate_value": round(0.70 + delta, 6),
        "delta": delta,
        "delta_lower_bound": round(delta - 0.01, 6),
        "delta_upper_bound": round(delta + 0.01, 6),
        "passed": passed,
    }


def header_minimal(model: type[CanonicalContract], seed: str) -> dict[str, Any]:
    """The header fields a contract cannot be constructed without, plus the pinned clock."""

    # A model with no ADR-055 prefix has no id space of its own, so its fixture id is a
    # readable label rather than a minted base32 body: inventing a prefix here would put a
    # shape in the fixtures that `ids.py` does not recognise.
    contract_id = (
        identifier(_PREFIX_FOR[model.ID_KIND], seed)
        if model.ID_KIND is not None
        else f"{snake_case(model.__name__).replace('_', '-')}-{seed}"
    )
    header: dict[str, Any] = {
        "contract_id": contract_id,
        "created_at": CREATED_AT,
        "created_by": PRINCIPAL,
        "workspace_id": WORKSPACE_ID,
    }
    if model.PROJECT_SCOPED:
        header["project_id"] = PROJECT_ID
    return header


def header_complete(
    model: type[CanonicalContract], seed: str, objective_ref: dict[str, Any] | None
) -> dict[str, Any]:
    """Every header field, all of them populated, including the four optional ones."""

    header = header_minimal(model, seed)
    header["project_id"] = PROJECT_ID
    header.update(
        {
            "contract_type": model.CONTRACT_TYPE,
            "schema_version": "1.0.0",
            "supersedes_contract_id": identifier(
                _PREFIX_FOR[model.ID_KIND] if model.ID_KIND else "obj", f"{seed}-superseded"
            ),
            "labels": {"owner": "routing-platform", "tier": "gold"},
            "retention_class": "STANDARD",
        }
    )
    if objective_ref is not None:
        header["objective_contract_ref"] = objective_ref
    return header


# The `ids.py` prefix behind each `ID_KIND`, mirrored here so the generator can mint an id
# with the right shape without importing the private prefix table.
_PREFIX_FOR: dict[str, str] = {
    "objective_contract": "obj",
    "node_contract": "nct",
    "verification_spec": "vsp",
    "routing_request": "rrq",
    "execution_configuration": "cfg",
    "configuration_candidate": "ccd",
    "compatibility_decision": "cmp",
    "routing_receipt": "rcp",
    "independent_verification_result": "ivr",
    "failure_event": "flr",
    "router_model_version": "rmv",
    "router_training_snapshot": "rts",
    "router_promotion_report": "rpr",
    "shadow_decision": "shd",
    "experience": "exp",
    # Added by the freeze delta of 5 Sep 2026 (ADR-060, ADR-061).
    "shadow_rollout_result": "shr",
    "router_activation": "rac",
}


def objective_contract_ref_json() -> dict[str, Any]:
    """A sealed ``ObjectiveContractRef`` document, for embedding in every complete header."""

    payload = {
        **header_minimal(ObjectiveContractRef, "embedded"),
        **OBJECTIVE_CONTRACT_REF_BODY,
    }
    return ObjectiveContractRef.model_validate(payload).model_dump(mode="json")


OBJECTIVE_CONTRACT_REF_BODY: dict[str, Any] = {
    "objective_contract_id": identifier("obj", "freeze"),
    "revision": 3,
    "objective_contract_hash": digest("objective-contract"),
    "verified_success_floor": 0.9,
    "utility_profile_id": "balanced-delivery",
    "risk_policy": POLICY_REF,
    "approved_by": PRINCIPAL,
    "approved_at": "2026-02-01T08:00:00Z",
}

NODE_CONTRACT_REF: dict[str, Any] = {
    "node_contract_id": identifier("nct", "freeze"),
    "immutable_hash": NODE_CONTRACT_IMMUTABLE_HASH,
}
VERIFICATION_SPEC_REF: dict[str, Any] = {
    "verification_spec_id": identifier("vsp", "freeze"),
    "content_hash": VERIFICATION_SPEC_HASH,
}
EXECUTION_CONFIGURATION_BODY: dict[str, Any] = {
    "environment": {"environment": ENVIRONMENT_REF, "workspace_isolation": "worktree"},
    "runtime": RUNTIME_REF,
    "model": {
        "model_id": "claude-opus-4",
        "provider": "CLAUDE",
        "inference_profile": {"temperature": 0.2, "thinking_budget": 8_000, "stream": True},
    },
    "tools": [
        {
            "capability": CAPABILITY_REF,
            "tool": TOOL_REF,
            "binding_id": identifier("cbd", "ripgrep"),
            "binding_version": "1.0.0",
        }
    ],
    "skills": [SKILL_REF],
    "verifier": {
        "verifier": VERIFIER_REF,
        "version": "4.2.0",
        "verification_spec_hash": VERIFICATION_SPEC_HASH,
    },
}
TASK_FEATURES_BODY_MINIMAL: dict[str, Any] = {
    "source_profile_id": identifier("prf", "freeze"),
    "risk": "MEDIUM",
    "irreversible_actions": False,
    "expected_horizon": "MEDIUM",
    "profile_confidence": 0.78,
}
TASK_FEATURES_BODY_COMPLETE: dict[str, Any] = {
    **TASK_FEATURES_BODY_MINIMAL,
    "complexity": 0.62,
    "structure_certainty": 0.55,
    "feedback_dependency": 0.7,
    "dependency_complexity": 0.4,
    "parallelism_potential": 0.3,
    "uncertainty": 0.45,
    "verifier_strength": 0.8,
}
PROJECT_FEATURES_BODY_MINIMAL: dict[str, Any] = {
    "feature_window_days": 90,
    "observed_task_count": 214,
}
PROJECT_FEATURES_BODY_COMPLETE: dict[str, Any] = {
    **PROJECT_FEATURES_BODY_MINIMAL,
    "mean_complexity": 0.58,
    "mean_uncertainty": 0.41,
    "mean_verifier_strength": 0.74,
    "irreversible_action_rate": 0.12,
    "maximum_risk": "HIGH",
    "dominant_expected_horizon": "MEDIUM",
}
GRAPH_FEATURES: dict[str, Any] = {
    "parent_node_types": ["TASK", "AGENT"],
    "child_node_types": ["VERIFIER"],
    "depth": 3,
    "critical_path": True,
    "retry_number": 1,
}
STRUCTURED_EXPLANATION_BODY_COMPLETE: dict[str, Any] = {
    "summary": (
        "Selected the sandboxed Claude configuration: highest verified-success lower "
        "bound within budget."
    ),
    "factors": [
        {
            "factor_id": "verified-success-lcb",
            "description": (
                "Lower confidence bound on node verified success exceeded the floor."
            ),
            "weight": 0.64,
            "evidence_refs": [EVIDENCE_REF],
        },
        {
            "factor_id": "cost-pressure",
            "description": "Predicted cost argued against this configuration and was outweighed.",
            "weight": -0.18,
            "evidence_refs": [SIMULATION_EVIDENCE_REF],
        },
    ],
    "rejected_candidates": [
        {
            "candidate_id": identifier("ccd", "rejected"),
            "stage": "JOINT_COMPATIBILITY",
            "reason_code": "VERIFIER_NOT_INDEPENDENT",
            "detail": "The cheaper tuple reused the producing runtime for verification.",
        }
    ],
}
CONTRACT_SIGNATURE: dict[str, Any] = {
    "node_kind": "TASK",
    "objective_digest": digest("objective-signature"),
    "capability_digest": digest("capability-signature"),
    "verification_spec_hash": VERIFICATION_SPEC_HASH,
    "risk_class": "MEDIUM_DIGITAL",
}
SNAPSHOT_SPLIT: dict[str, Any] = {
    "training_project_ids": [identifier("prj", "train-a"), identifier("prj", "train-b")],
    "validation_project_ids": [identifier("prj", "validate")],
    "holdout_project_ids": [identifier("prj", "holdout")],
}
SHADOW_SUMMARY: dict[str, Any] = {
    "decision_count": 1_842,
    "agreement_rate": 0.87,
    "projected_utility_delta": 0.031,
    "sample_size": 1_842,
}

# ADR-060. A branched rollout runs two arms of the *same* node: the CONTROL fork re-runs the
# configuration the live router chose — `CONFIGURATION_HASH`, the one every other fixture in
# this file already names — and the SHADOW fork runs the candidate router's choice, which is
# a different configuration and therefore a different digest. Reusing one hash for both arms
# would have made the pair a comparison of a configuration against itself.
SHADOW_CONFIGURATION_HASH = digest("shadow-configuration")

ROLLOUT_SEED = 20_260_301
"""The seed policy both arms of a pair share; the field that makes "same seed" checkable."""

CONTROL_SERVING: dict[str, Any] = {
    "provider": "FAKE",
    "runtime_version": "2026.03.01",
    "model_id": "fake-small",
}
SHADOW_SERVING: dict[str, Any] = {
    "provider": "CLAUDE",
    "runtime_version": "2026.03.01",
    "model_id": "claude-router-eval",
    "serving_labels": {"temperature": "0.0", "quantization": "bf16", "seed": "20260301"},
}
ROLLBACK_CAUSE = (
    "Critical cohort regression on secrets handling; the workspace was withdrawn to the "
    "last version whose drill passed."
)


def _minimal_bodies(objective_ref: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The body of each ``minimal.json``: nothing the model can do without, nothing more."""

    return {
        "ObjectiveContract": {
            "goal": "Ship the routing freeze with no behaviour change.",
            "scope_in": ["contracts", "fixtures"],
            "verified_success_floor": 0.9,
            "false_acceptance_ceiling": 0.02,
            "utility_weights": UTILITY_WEIGHTS,
            "risk_policy_ref": POLICY_REF,
            "resource_budget": RESOURCE_BUDGET,
            "revision": 1,
            "approval_receipt_ref": APPROVAL_ARTIFACT_REF,
        },
        "ObjectiveContractRef": dict(OBJECTIVE_CONTRACT_REF_BODY),
        "NodeContract": {
            # Not a body field: the header's `objective_contract_ref` is optional in the
            # type and required by `NodeContract`'s validator, so a minimal node contract
            # cannot omit it. That is exactly what the minimality test asserts.
            "objective_contract_ref": objective_ref,
            "node_id": "implement-migration",
            "run_graph_id": identifier("rgr", "freeze"),
            "graph_revision": 4,
            "execution_instance_id": identifier("run", "freeze"),
            "objective": "Write the additive migration and prove it reverses.",
            "node_kind": "TASK",
            "allowed_risk_class": "MEDIUM_DIGITAL",
            "resource_cap": RESOURCE_BUDGET,
            "verification_spec_ref": VERIFICATION_SPEC_REF,
            "failure_policy_ref": FAILURE_POLICY_REF,
        },
        "VerificationSpec": {
            "revision": 2,
            "claims": [
                {
                    "claim_id": "migration-reverses",
                    "description": "upgrade head, downgrade base, upgrade head runs clean.",
                    "criticality": "REQUIRED",
                    "required_evidence_types": ["DIGITAL"],
                }
            ],
            "accepted_outcomes": ["PASS", "FAIL"],
        },
        "TaskFeatures": dict(TASK_FEATURES_BODY_MINIMAL),
        "ProjectFeatures": dict(PROJECT_FEATURES_BODY_MINIMAL),
        "RoutingContext": {
            "node_contract_ref": NODE_CONTRACT_REF,
            "task_features": {
                **header_minimal(TaskFeatures, "nested-minimal"),
                **TASK_FEATURES_BODY_MINIMAL,
            },
            "graph_features": GRAPH_FEATURES,
            "project_features": {
                **header_minimal(ProjectFeatures, "nested-minimal"),
                **PROJECT_FEATURES_BODY_MINIMAL,
            },
            "available_runtime_snapshot_id": identifier("mcp", "runtime-snapshot"),
            "capability_registry_snapshot_id": identifier("mcp", "capability-snapshot"),
            "connection_availability_snapshot_id": identifier("mcp", "connection-snapshot"),
            "policy_snapshot_id": identifier("pol", "snapshot"),
            "workspace_router_version": identifier("rmv", "active"),
            "requested_at": "2026-03-01T08:59:30Z",
        },
        "ExecutionConfiguration": dict(EXECUTION_CONFIGURATION_BODY),
        "ConfigurationCandidate": {
            "routing_request_id": identifier("rrq", "freeze"),
            "configuration": {
                **header_minimal(ExecutionConfiguration, "nested-minimal"),
                **EXECUTION_CONFIGURATION_BODY,
            },
            "construction_stage": "RANK_BY_UTILITY",
            "hard_eligible": True,
            "predicted": PREDICTED_OUTCOMES,
            "uncertainty_score": 0.14,
            "lower_confidence_success": 0.81,
        },
        "CompatibilityDecision": {
            "subject_type": "VERIFIER",
            "subject_ref": "diff-and-suite@4.2.0",
            "status": "COMPATIBLE",
            "rule_id": "verifier-independence",
            "rule_version": "2.0.0",
            "reason_code": "DISTINCT_RUNTIME",
            "evaluated_at": "2026-03-01T08:59:45Z",
        },
        "StructuredExplanation": {
            "summary": "Only one tuple cleared the verified-success gate inside budget.",
        },
        "RoutingDecisionReceipt": {
            "routing_request_id": identifier("rrq", "freeze"),
            "node_contract_hash": NODE_CONTRACT_IMMUTABLE_HASH,
            "decision_type": "HUMAN_REVIEW_REQUIRED",
            "uncertainty": {
                "epistemic_uncertainty": 0.31,
                "lower_confidence_success": 0.44,
                "calibration_version": "conformal-v1",
            },
            "workspace_router_version": identifier("rmv", "active"),
            "objective_contract_version": 3,
            "capability_registry_snapshot_id": identifier("mcp", "capability-snapshot"),
            "policy_snapshot_id": identifier("pol", "snapshot"),
            "explanation": {
                **header_minimal(StructuredExplanation, "nested-minimal"),
                "summary": (
                    "No candidate cleared the gate and no audited fallback was compatible."
                ),
            },
        },
        "IndependentVerificationResult": {
            "execution_instance_id": identifier("run", "freeze"),
            "verification_spec_hash": VERIFICATION_SPEC_HASH,
            "verifier": VERIFIER_REF,
            "verifier_version": "4.2.0",
            "status": "INCONCLUSIVE",
            "signed_at": "2026-03-01T09:14:00Z",
        },
        "ExperienceRecord": {
            "visibility": "PROJECT",
            "source_node_execution_id": identifier("run", "experience-source"),
            "contract_signature": CONTRACT_SIGNATURE,
            "configuration_hash": CONFIGURATION_HASH,
            "local_verification_status": "PASS",
            "attribution": {"confidence": 0.6, "method_version": "dependency-heuristic-v1"},
            "outcomes": {"cost": "3.75", "latency_ms": 38_000},
            "permission_provenance": {**PERMISSION_PROVENANCE, "scope": "PROJECT"},
        },
        "FailureEvent": {
            "execution_instance_id": identifier("run", "freeze"),
            "failure_type": "CONFIGURATION",
            "affected_layer": "tool-binding",
            "retryable": True,
            "classification_confidence": 0.72,
            "assigned_owner": "CONFIGURATION",
            "recommended_action": {
                "action_code": "REBIND_TOOL",
                "owner": "CONFIGURATION",
                "rationale": "Rebind the capability to the surviving connector.",
                "retry_allowed": True,
            },
        },
        "RouterModelVersion": {
            "scope": "TEAM_WORKSPACE",
            "algorithm_id": "gradient-boosted-ranker",
            "feature_schema_version": "1.0.0",
            "training_snapshot_id": identifier("rts", "freeze"),
            "artifact_digest": digest("router-artifact"),
            "calibration_artifact_digest": digest("router-calibration"),
            "status": "CANDIDATE",
        },
        "RouterTrainingSnapshot": {
            "included_experience_ids": [
                identifier("exp", "one"),
                identifier("exp", "two"),
            ],
            "permission_proof": PERMISSION_PROVENANCE,
            "contradiction_treatment": (
                "OPEN contradictions excluded; RESOLVED kept with their adjudication."
            ),
            "deduplication_rule": "One record per (configuration_hash, source_node_execution_id).",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-03-01T00:00:00Z",
            "split": SNAPSHOT_SPLIT,
        },
        "RouterPromotionReport": {
            "candidate_version": identifier("rmv", "candidate"),
            "baseline_version": identifier("rmv", "baseline"),
            "training_snapshot_id": identifier("rts", "freeze"),
            "holdout_definition_id": identifier("rts", "holdout"),
            "primary_metric_result": comparison("constrained-regret", -0.04, True),
            "verified_success_non_regression": comparison("verified-success", 0.02, True),
            "false_acceptance_non_regression": comparison("false-acceptance", -0.01, True),
            "calibration_result": comparison("expected-calibration-error", -0.02, True),
            "shadow_result": SHADOW_SUMMARY,
            "rollback_target": identifier("rmv", "baseline"),
            "decision": "REJECT",
        },
        "ShadowDecision": {
            "executed_receipt_id": identifier("rcp", "executed"),
            "shadow_receipt_id": identifier("rcp", "shadow"),
            "shadow_router_version_id": identifier("rmv", "candidate"),
            "agreement": False,
            "projected_utility_delta": 0.018,
            "comparison_notes": (
                "Shadow selected a cheaper runtime the executed decision rejected."
            ),
            "evaluated_at": "2026-03-01T09:20:00Z",
        },
        "ShadowRolloutResult": {
            # `identifier("shd", "minimal")` is the `contract_id` of
            # `shadow_decision/minimal.json`, because `header_minimal` seeds it with
            # "minimal": a rollout scores a decision, and the reference is worth more when
            # it resolves to a document the reader can open than when it names a string.
            "shadow_decision_id": identifier("shd", "minimal"),
            "kind": "CONTROL",
            "fork_execution_id": identifier("rtc", "control-fork"),
            "configuration_hash": CONFIGURATION_HASH,
            # `serving_labels` is omitted rather than written as `{}`: it defaults, so a
            # minimal document that spelled it out would be carrying a field the model can
            # do without — the same rule every other minimal body here follows.
            "serving": dict(CONTROL_SERVING),
            # `verified` is false, so `verification_result_id` is absent and `false_accept`
            # would be refused: an acceptance that was never made cannot have been false.
            "observed": {
                "quality": 0.71,
                "cost": 0.42,
                "latency_ms": 18_400.0,
                "verified": False,
            },
            "budget_consumed": 0.42,
            "trial_index": 0,
            "seed": ROLLOUT_SEED,
            "completed_at": "2026-03-01T09:31:00Z",
        },
        "RouterActivation": {
            # Sequence 1 is the first entry in a ledger, so it displaces nothing and names
            # no predecessor; `PROJECT_SCOPED` is False, so the header carries no
            # `project_id` and the scope has to be the workspace prior.
            "scope": "TEAM_WORKSPACE",
            "family_key": "gradient-boosted-ranker",
            "sequence": 1,
            "kind": "PROMOTE",
            "router_version_id": identifier("rmv", "candidate"),
            "approved_by": PRINCIPAL,
        },
    }


def _complete_bodies(objective_ref: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The body of each ``complete.json``: every field, none of them null or empty."""

    del objective_ref  # the complete header already carries it; bodies never restate it
    return {
        "ObjectiveContract": {
            "goal": "Route every node to a configuration whose verified success clears the floor.",
            "scope_in": ["node-routing", "verification-feedback"],
            "scope_out": ["graph-planning", "robotics"],
            "verified_success_floor": 0.92,
            "false_acceptance_ceiling": 0.015,
            "utility_weights": UTILITY_WEIGHTS,
            "risk_policy_ref": POLICY_REF,
            "resource_budget": RESOURCE_BUDGET,
            "required_human_approvals": [
                {
                    "approval_id": "physical-action",
                    "description": "A workspace owner approves anything above HIGH_DIGITAL.",
                    "required_role": "OWNER",
                    "applies_above_risk_class": "HIGH_DIGITAL",
                }
            ],
            "revision": 3,
            "approval_receipt_ref": APPROVAL_ARTIFACT_REF,
            # Added by the freeze delta of 5 Sep 2026 (OQ-410, ADR-062): additive, optional
            # and defaulted to None, so the minimal body above still omits it — and still
            # seals to a new digest, because ADR-056's canonical form keeps nulls.
            "exploration_policy": {"alpha": 0.05, "max_explore_count": 200, "max_cost": 25.0},
        },
        "ObjectiveContractRef": dict(OBJECTIVE_CONTRACT_REF_BODY),
        "NodeContract": {
            "node_id": "implement-migration",
            "run_graph_id": identifier("rgr", "freeze"),
            "graph_revision": 4,
            "execution_instance_id": identifier("run", "freeze"),
            "objective": "Write the additive migration and prove it reverses.",
            "node_kind": "TASK",
            "input_contracts": [
                {
                    "schema_id": "migration-request",
                    "version": "1.0.0",
                    "content_digest": digest("input-schema"),
                }
            ],
            "output_contracts": [
                {
                    "schema_id": "migration-result",
                    "version": "1.0.0",
                    "content_digest": digest("output-schema"),
                }
            ],
            "required_capabilities": [
                {
                    "capability": CAPABILITY_REF,
                    "version_range": ">=1.2.0,<2.0.0",
                    "required_scope": "workspace:files:read",
                }
            ],
            "evidence_requirements": [
                {
                    "requirement_id": "suite-output",
                    "evidence_class": "DIGITAL",
                    "minimum_count": 1,
                    "description": "The migration suite must produce machine-readable output.",
                }
            ],
            "environment_constraints": [
                {
                    "constraint_id": "no-egress",
                    "attribute": "network_egress",
                    "operator": "EQ",
                    "value": "denied",
                    "rationale": "Migrations never reach the network.",
                }
            ],
            "allowed_risk_class": "MEDIUM_DIGITAL",
            "resource_cap": RESOURCE_BUDGET,
            "verification_spec_ref": VERIFICATION_SPEC_REF,
            "failure_policy_ref": FAILURE_POLICY_REF,
        },
        "VerificationSpec": {
            "revision": 2,
            "claims": [
                {
                    "claim_id": "migration-reverses",
                    "description": "upgrade head, downgrade base, upgrade head runs clean.",
                    "criticality": "REQUIRED",
                    "required_evidence_types": ["DIGITAL"],
                },
                {
                    "claim_id": "no-data-loss",
                    "description": "No column holding the only copy of a value is dropped.",
                    "criticality": "SUPPORTING",
                    "required_evidence_types": ["DIGITAL", "HUMAN_ATTESTATION"],
                },
            ],
            "metrics": [
                {
                    "metric_id": "suite-pass-rate",
                    "operator": "GTE",
                    "threshold": 1.0,
                    "evaluator_contract": "pytest-summary-v1",
                },
                {
                    "metric_id": "reviewer-verdict",
                    "operator": "CUSTOM",
                    "threshold": "APPROVED",
                    "evaluator_contract": "human-review-v2",
                },
            ],
            "independence": {
                "producer_cannot_self_accept": True,
                "separate_context_required": True,
                "distinct_runtime_preferred": True,
            },
            "accepted_outcomes": ["PASS", "FAIL", "INCONCLUSIVE"],
        },
        "TaskFeatures": dict(TASK_FEATURES_BODY_COMPLETE),
        "ProjectFeatures": dict(PROJECT_FEATURES_BODY_COMPLETE),
        "RoutingContext": {
            "node_contract_ref": NODE_CONTRACT_REF,
            "feature_schema_version": "1.0.0",
            "task_features": {
                **header_complete(TaskFeatures, "nested-complete", None),
                **TASK_FEATURES_BODY_COMPLETE,
            },
            "graph_features": GRAPH_FEATURES,
            "project_features": {
                **header_complete(ProjectFeatures, "nested-complete", None),
                **PROJECT_FEATURES_BODY_COMPLETE,
            },
            "available_runtime_snapshot_id": identifier("mcp", "runtime-snapshot"),
            "capability_registry_snapshot_id": identifier("mcp", "capability-snapshot"),
            "connection_availability_snapshot_id": identifier("mcp", "connection-snapshot"),
            "policy_snapshot_id": identifier("pol", "snapshot"),
            "workspace_router_version": identifier("rmv", "active"),
            "project_adapter_version": identifier("rmv", "adapter"),
            "historical_experience_refs": [identifier("exp", "one"), identifier("exp", "two")],
            "requested_at": "2026-03-01T08:59:30Z",
        },
        "ExecutionConfiguration": dict(EXECUTION_CONFIGURATION_BODY),
        "ConfigurationCandidate": {
            "routing_request_id": identifier("rrq", "freeze"),
            "configuration": {
                **header_complete(ExecutionConfiguration, "nested-complete", None),
                **EXECUTION_CONFIGURATION_BODY,
            },
            "construction_stage": "RANK_BY_UTILITY",
            "hard_eligible": True,
            "compatibility_decision_refs": [
                identifier("cmp", "runtime"),
                identifier("cmp", "verifier"),
            ],
            "predicted": PREDICTED_OUTCOMES,
            "uncertainty_score": 0.14,
            "lower_confidence_success": 0.81,
            "utility_score": 0.66,
            "pareto_dominated": False,
            "fallback_eligible": True,
        },
        "CompatibilityDecision": {
            "subject_type": "VERIFIER",
            "subject_ref": "diff-and-suite@4.2.0",
            "status": "COMPATIBLE",
            "rule_id": "verifier-independence",
            "rule_version": "2.0.0",
            "reason_code": "DISTINCT_RUNTIME",
            "evidence_refs": [EVIDENCE_REF, SIMULATION_EVIDENCE_REF],
            "evaluated_at": "2026-03-01T08:59:45Z",
        },
        "StructuredExplanation": dict(STRUCTURED_EXPLANATION_BODY_COMPLETE),
        "RoutingDecisionReceipt": {
            "routing_request_id": identifier("rrq", "freeze"),
            "node_contract_hash": NODE_CONTRACT_IMMUTABLE_HASH,
            "selected_configuration_id": identifier("cfg", "freeze"),
            "selected_configuration_hash": CONFIGURATION_HASH,
            "decision_type": "EXPLORE",
            "selection_propensity": 0.12,
            "predicted_outcomes": PREDICTED_OUTCOMES,
            "uncertainty": {
                "epistemic_uncertainty": 0.19,
                "lower_confidence_success": 0.81,
                "calibration_version": "conformal-v1",
            },
            "candidate_summary_refs": [identifier("ccd", "first"), identifier("ccd", "second")],
            "rejected_candidate_reasons": [
                {
                    "candidate_id": identifier("ccd", "rejected"),
                    "stage": "SUCCESS_GATE",
                    "reason_code": "BELOW_SUCCESS_FLOOR",
                    "detail": "Lower confidence bound sat under the objective floor.",
                }
            ],
            "experience_refs": [identifier("exp", "one")],
            "workspace_router_version": identifier("rmv", "active"),
            "project_adapter_version": identifier("rmv", "adapter"),
            "objective_contract_version": 3,
            "capability_registry_snapshot_id": identifier("mcp", "capability-snapshot"),
            "policy_snapshot_id": identifier("pol", "snapshot"),
            "fallback_configuration_id": identifier("cfg", "fallback"),
            "explanation": {
                **header_complete(StructuredExplanation, "nested-complete", None),
                **STRUCTURED_EXPLANATION_BODY_COMPLETE,
            },
        },
        "IndependentVerificationResult": {
            "execution_instance_id": identifier("run", "freeze"),
            "verification_spec_hash": VERIFICATION_SPEC_HASH,
            "verifier": VERIFIER_REF,
            "verifier_version": "4.2.0",
            "status": "INCONCLUSIVE",
            "claim_results": [
                {
                    "claim_id": "migration-reverses",
                    "status": "PASS",
                    "evidence_refs": [EVIDENCE_REF],
                    "coverage": 1.0,
                    "confidence": 0.99,
                    "limitations": [],
                },
                {
                    "claim_id": "no-data-loss",
                    "status": "INCONCLUSIVE",
                    "evidence_refs": [SIMULATION_EVIDENCE_REF],
                    "coverage": 0.4,
                    "confidence": 0.55,
                    "limitations": ["Only the migrated columns were sampled."],
                },
            ],
            "deterministic_evidence_refs": [EVIDENCE_REF],
            "model_review_refs": [SIMULATION_EVIDENCE_REF],
            "conflict_refs": [identifier("ivr", "conflicting")],
            "source_verification_id": identifier("ver", "v01-source"),
            "signed_at": "2026-03-01T09:14:00Z",
        },
        "ExperienceRecord": {
            "visibility": "TEAM_WORKSPACE",
            "source_node_execution_id": identifier("run", "experience-source"),
            "contract_signature": CONTRACT_SIGNATURE,
            "configuration_hash": CONFIGURATION_HASH,
            "local_verification_status": "PASS",
            "final_run_status": "PASS",
            "attribution": {
                "score": 0.41,
                "confidence": 0.6,
                "method_version": "dependency-heuristic-v1",
            },
            "outcomes": {"quality": 0.86, "cost": "3.75", "latency_ms": 38_000},
            "failure_type": "TRANSIENT",
            "contradiction_status": "RESOLVED",
            "evidence_refs": [EVIDENCE_REF],
            "permission_provenance": PERMISSION_PROVENANCE,
            "eligible_for_learning": True,
        },
        "FailureEvent": {
            "execution_instance_id": identifier("run", "freeze"),
            "failure_type": "CONFIGURATION",
            "affected_layer": "tool-binding",
            "retryable": True,
            "classification_confidence": 0.72,
            "evidence_refs": [EVIDENCE_REF],
            "attempted_configuration_hashes": [CONFIGURATION_HASH, digest("failed-configuration")],
            "assigned_owner": "CONFIGURATION",
            "recommended_action": {
                "action_code": "REBIND_TOOL",
                "owner": "CONFIGURATION",
                "rationale": "Rebind the capability to the surviving connector.",
                "retry_allowed": True,
            },
        },
        "RouterModelVersion": {
            "scope": "PROJECT_ADAPTER",
            "algorithm_id": "gradient-boosted-ranker",
            "feature_schema_version": "1.0.0",
            "training_snapshot_id": identifier("rts", "freeze"),
            "artifact_digest": digest("router-artifact"),
            "calibration_artifact_digest": digest("router-calibration"),
            "parent_version_id": identifier("rmv", "baseline"),
            "status": "SHADOW",
        },
        "RouterTrainingSnapshot": {
            "included_experience_ids": [identifier("exp", "one"), identifier("exp", "two")],
            "permission_proof": PERMISSION_PROVENANCE,
            "contract_schema_version": "1.0.0",
            "feature_schema_version": "1.0.0",
            "excluded_contradiction_statuses": ["OPEN"],
            "contradiction_treatment": (
                "OPEN contradictions excluded; RESOLVED kept with their adjudication."
            ),
            "deduplication_rule": (
                "One record per (configuration_hash, source_node_execution_id)."
            ),
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-03-01T00:00:00Z",
            "provider_version_boundaries": {
                "CLAUDE": "<=claude-opus-4",
                "CODEX": "<=codex-1.4",
            },
            "split": SNAPSHOT_SPLIT,
        },
        "RouterPromotionReport": {
            "candidate_version": identifier("rmv", "candidate"),
            "baseline_version": identifier("rmv", "baseline"),
            "training_snapshot_id": identifier("rts", "freeze"),
            "holdout_definition_id": identifier("rts", "holdout"),
            "primary_metric_result": comparison("constrained-regret", -0.04, True),
            "verified_success_non_regression": comparison("verified-success", 0.02, True),
            "false_acceptance_non_regression": comparison("false-acceptance", 0.03, False),
            "calibration_result": comparison("expected-calibration-error", -0.02, True),
            "cohort_results": [
                {
                    "cohort_id": "high-risk",
                    "description": "Nodes whose allowed risk class is HIGH_DIGITAL or above.",
                    "sample_size": 412,
                    "critical": True,
                    "comparison": comparison("verified-success", 0.01, True),
                }
            ],
            "shadow_result": SHADOW_SUMMARY,
            "critical_regressions": [
                {
                    "finding_id": "false-acceptance-up",
                    "metric_id": "false-acceptance",
                    "severity": "ERROR",
                    "description": "False acceptance rose on the verifier-conflict cohort.",
                    "disclosed_bound": "+0.03 [+0.02, +0.04]",
                }
            ],
            "noncritical_tradeoffs": [
                {
                    "finding_id": "latency-up",
                    "metric_id": "latency-p95",
                    "severity": "WARNING",
                    "description": "p95 latency rose while quality improved.",
                    "disclosed_bound": "+1.4s [+0.9s, +1.9s]",
                }
            ],
            "rollback_target": identifier("rmv", "baseline"),
            "decision": "REQUIRE_REVIEW",
            "approved_by": PRINCIPAL,
        },
        "ShadowDecision": {
            "executed_receipt_id": identifier("rcp", "executed"),
            "shadow_receipt_id": identifier("rcp", "shadow"),
            "shadow_router_version_id": identifier("rmv", "candidate"),
            "executed_configuration_hash": CONFIGURATION_HASH,
            "shadow_configuration_hash": CONFIGURATION_HASH,
            "agreement": True,
            "projected_utility_delta": 0.018,
            "comparison_notes": "Shadow and executed decisions selected the same tuple.",
            "evaluated_at": "2026-03-01T09:20:00Z",
        },
        "ShadowRolloutResult": {
            # The `contract_id` of `shadow_decision/complete.json`, for the same reason the
            # minimal body names the minimal decision.
            "shadow_decision_id": identifier("shd", "complete"),
            "kind": "SHADOW",
            "fork_execution_id": identifier("rtc", "shadow-fork"),
            "configuration_hash": SHADOW_CONFIGURATION_HASH,
            "serving": dict(SHADOW_SERVING),
            "verification_result_id": identifier("ivr", "rollout"),
            "observed": {
                "quality": 0.88,
                "cost": 0.51,
                "latency_ms": 16_250.0,
                "verified": True,
                "false_accept": False,
            },
            "budget_consumed": 0.51,
            "trial_index": 7,
            "seed": ROLLOUT_SEED,
            "completed_at": "2026-03-01T09:29:00Z",
        },
        "RouterActivation": {
            # A ROLLBACK is the only kind that populates every field: it restores a version
            # (`router_version_id` == `rollback_target_version_id`), displaces the one that
            # regressed, and states why. The complete header always carries a `project_id`,
            # so the scope has to be the adapter.
            "scope": "PROJECT_ADAPTER",
            "family_key": "gradient-boosted-ranker",
            "sequence": 4,
            "kind": "ROLLBACK",
            "router_version_id": identifier("rmv", "baseline"),
            "previous_version_id": identifier("rmv", "candidate"),
            "rollback_target_version_id": identifier("rmv", "baseline"),
            "promotion_report_id": identifier("rpr", "freeze"),
            "approved_by": PRINCIPAL,
            "cause": ROLLBACK_CAUSE,
        },
    }


def _invalidate(document: dict[str, Any], **replacements: Any) -> dict[str, Any]:
    """Apply top-level replacements to a copy of ``document``."""

    return {**document, **replacements}


def _invalid_documents(complete: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One named violation per model, with the error substring each one must produce.

    Every violation is chosen to exercise the rule that model actually owns, so that a
    validator quietly deleted during a refactor takes its ``invalid`` fixture down with it.
    Twelve of the twenty-one trip a hand-written ``@model_validator`` on the contract
    itself; the rest trip a field constraint, an enum, the derived digest sealed by
    ``seal_derived_hashes``, or the header rules on the base class.
    """

    def with_nested(name: str, field: str, **replacements: Any) -> dict[str, Any]:
        document = dict(complete[name])
        document[field] = {**document[field], **replacements}
        return document

    documents: dict[str, dict[str, Any]] = {
        # Field constraint: a success floor above 1 is not a probability.
        "ObjectiveContract": _invalidate(
            complete["ObjectiveContract"], verified_success_floor=1.5
        ),
        # Field constraint: revisions start at 1, so revision 0 names nothing.
        "ObjectiveContractRef": _invalidate(complete["ObjectiveContractRef"], revision=0),
        # Enum: the SDD's `PHYSICAL` was superseded by registry §5.3's RiskClass.
        "NodeContract": _invalidate(complete["NodeContract"], allowed_risk_class="PHYSICAL"),
        # OQ-418: the independence flags are Literal[True] and cannot be turned off.
        "VerificationSpec": with_nested(
            "VerificationSpec", "independence", producer_cannot_self_accept=False
        ),
        # Field constraint: a normalised feature above 1 is out of its declared range.
        "TaskFeatures": _invalidate(complete["TaskFeatures"], complexity=1.5),
        # Model validator: aggregates over an empty window are a guess.
        "ProjectFeatures": _invalidate(complete["ProjectFeatures"], observed_task_count=0),
        # Field pattern: the feature schema version is semver, not a two-part version.
        "RoutingContext": _invalidate(complete["RoutingContext"], feature_schema_version="1.0"),
        # Derived digest: a configuration hash that does not match its own signature.
        "ExecutionConfiguration": _invalidate(
            complete["ExecutionConfiguration"], configuration_hash=digest("wrong-signature")
        ),
        # Model validator: a hard-ineligible candidate cannot be the audited fallback.
        "ConfigurationCandidate": _invalidate(
            complete["ConfigurationCandidate"], hard_eligible=False, fallback_eligible=True
        ),
        # Enum: three statuses exist and `MAYBE` is not one of them.
        "CompatibilityDecision": _invalidate(complete["CompatibilityDecision"], status="MAYBE"),
        # Model validator: a repeated factor id double-counts a reason.
        "StructuredExplanation": _invalidate(
            complete["StructuredExplanation"],
            factors=[
                complete["StructuredExplanation"]["factors"][0],
                complete["StructuredExplanation"]["factors"][0],
            ],
        ),
        # Model validator: receipts refuse secret-shaped values rather than redacting them.
        "RoutingDecisionReceipt": _invalidate(
            complete["RoutingDecisionReceipt"],
            labels={"owner": "routing-platform", "authorization": "Bearer not-a-real-token"},
        ),
        # Model validator: an aggregate PASS cannot contain a claim that did not pass. The
        # complete document already carries an INCONCLUSIVE claim, so raising the aggregate
        # to PASS is the whole violation and no second field has to move.
        "IndependentVerificationResult": _invalidate(
            complete["IndependentVerificationResult"], status="PASS"
        ),
        # Header rule: the projection is keyed by the P7 experience id and must carry `exp_`.
        "ExperienceRecord": _invalidate(
            complete["ExperienceRecord"], contract_id=identifier("run", "wrong-prefix")
        ),
        # Model validator: SAFETY stops automatic recovery, so it cannot be retryable.
        "FailureEvent": _invalidate(complete["FailureEvent"], assigned_owner="SAFETY"),
        # Model validator: a workspace prior belongs to no single project.
        "RouterModelVersion": _invalidate(complete["RouterModelVersion"], scope="TEAM_WORKSPACE"),
        # Model validator: a snapshot over an empty window observed nothing.
        "RouterTrainingSnapshot": _invalidate(
            complete["RouterTrainingSnapshot"], window_end="2025-12-01T00:00:00Z"
        ),
        # Model validator: a critical regression blocks promotion.
        "RouterPromotionReport": _invalidate(complete["RouterPromotionReport"], decision="PROMOTE"),
        # Model validator: agreement must match the two hashes it summarises.
        "ShadowDecision": _invalidate(complete["ShadowDecision"], agreement=False),
        # Model validator: a fork that scored itself verified is a self-report unless it
        # names the independent verification that produced the verdict.
        "ShadowRolloutResult": _invalidate(
            complete["ShadowRolloutResult"], verification_result_id=None
        ),
        # Model validator: a withdrawal that does not say why is not an auditable reversal.
        "RouterActivation": _invalidate(complete["RouterActivation"], cause=None),
    }
    return documents


INVALID_EXPECTATIONS: dict[str, str] = {
    "ObjectiveContract": "less than or equal to 1",
    "ObjectiveContractRef": "greater than or equal to 1",
    "NodeContract": "Input should be 'LOW_DIGITAL'",
    "VerificationSpec": "Input should be True",
    "TaskFeatures": "less than or equal to 1",
    "ProjectFeatures": "an aggregate over no tasks",
    "RoutingContext": "String should match pattern",
    "ExecutionConfiguration": "does not match the digest of this configuration's signature",
    "ConfigurationCandidate": "cannot be the audited fallback",
    "CompatibilityDecision": "Input should be 'COMPATIBLE'",
    "StructuredExplanation": "repeats a factor_id",
    "RoutingDecisionReceipt": "secret-shaped value",
    "IndependentVerificationResult": "status is PASS while claims",
    "ExperienceRecord": "identity prefix required by ADR-055",
    "FailureEvent": "stops automatic recovery",
    "RouterModelVersion": "belongs to no single project",
    "RouterTrainingSnapshot": "is not after window_start",
    "RouterPromotionReport": "critical correctness or safety regression",
    "ShadowDecision": "say otherwise",
    "ShadowRolloutResult": "no verification_result_id is named",
    "RouterActivation": "a ROLLBACK activation leaves",
}
"""The substring each ``invalid.json`` records under ``_expect`` and the tests assert on."""


def build_documents() -> dict[str, dict[str, dict[str, Any]]]:
    """Build all four fixture documents for all twenty-one contracts."""

    objective_ref = objective_contract_ref_json()
    minimal_bodies = _minimal_bodies(objective_ref)
    complete_bodies = _complete_bodies(objective_ref)

    complete_documents: dict[str, dict[str, Any]] = {}
    minimal_documents: dict[str, dict[str, Any]] = {}
    for model in CONTRACT_INVENTORY:
        name = model.__name__
        minimal_input = {**header_minimal(model, "minimal"), **minimal_bodies[name]}
        # The digest is taken from the *validated* model, so `minimal.json` records the hash
        # of the document as it is after defaults are applied — which is the document a
        # reader ends up with, and therefore the only digest worth committing.
        minimal_documents[name] = {
            **minimal_input,
            "content_hash": model.model_validate(minimal_input).content_hash,
        }
        complete_input = {
            **header_complete(model, "complete", objective_ref),
            **complete_bodies[name],
        }
        complete_documents[name] = model.model_validate(complete_input).model_dump(mode="json")

    invalid_documents = _invalid_documents(complete_documents)
    documents: dict[str, dict[str, dict[str, Any]]] = {}
    for model in CONTRACT_INVENTORY:
        name = model.__name__
        invalid = dict(invalid_documents[name])
        # An invalid document is not a sealed one. Dropping the stale digest is what keeps
        # each `invalid.json` failing for the violation it names rather than for its hash.
        invalid.pop("content_hash", None)
        invalid["_expect"] = INVALID_EXPECTATIONS[name]
        documents[name] = {
            "minimal": minimal_documents[name],
            "complete": complete_documents[name],
            "invalid": invalid,
            "unknown_version": {
                **complete_documents[name],
                "schema_version": UNKNOWN_MAJOR_VERSION,
            },
        }
    return documents


def main() -> None:
    """Write all eighty-four fixture files, deterministically and with a trailing newline."""

    documents = build_documents()
    written = 0
    for model in CONTRACT_INVENTORY:
        directory = FIXTURE_ROOT / snake_case(model.__name__)
        directory.mkdir(parents=True, exist_ok=True)
        for kind in FIXTURE_KINDS:
            target = directory / f"{kind}.json"
            target.write_text(
                json.dumps(documents[model.__name__][kind], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            written += 1
    print(f"wrote {written} fixtures for {len(CONTRACT_INVENTORY)} contracts under {FIXTURE_ROOT}")


if __name__ == "__main__":
    main()
