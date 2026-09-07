# Revision-4 research contract reference

**Document revision:** 4  
**Status:** Executable projection field guide; full production owners remain authoritative

All fields below are required unless their schema explicitly accepts null. Null is absence, never a fabricated proof. Arrays may be empty only where the stage permits it. Every object rejects unknown fields, and every projection has a canonical payload content hash. These schemas are targeted research payloads, not full inherited owner headers.

The six amended records use schema 2.0.0; new records use 1.0.0. Original schemas and fixtures remain in the historical package. Ordinary data-role reuse is rejected; a reuse-capable theorem would require a distinct reviewed protocol and versioned extension. The fixed-sample verifier fixture uses a one-sided Clopper–Pearson bound and does not validate a clustered/adaptive analysis.

## ResearchStudyPlan

Schema version: `2.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `2.0.0` |
| `study_id` | string |
| `release` | v1.1.0 / v1.2.0 / v1.3.0 / v1.4.0 / v1.5.0 / v1.6.0 / v1.7.0 / v1.8.0 |
| `stage` | FEASIBILITY / PILOT / CONFIRMATORY |
| `protocol_ref` | Immutable locator; resolve owner/header/scope at integration |
| `verifier_qualification_ref` | Immutable locator; resolve owner/header/scope at integration |
| `target_population_ref` | Immutable locator; resolve owner/header/scope at integration |
| `sampling_plan_ref` | Immutable locator; resolve owner/header/scope at integration |
| `holdout_plan_ref` | Immutable locator; resolve owner/header/scope at integration |
| `cost_ledger_ref` | Immutable locator; resolve owner/header/scope at integration |
| `drift_plan_ref` | Typed value or null; owning stage determines applicability |
| `candidate_count` | integer |
| `final_hidden_max_uses` | Exact value: `1` |
| `selection_regret_claim` | boolean |
| `content_hash` | SHA-256 of payload excluding this field |
| `data_role_plan_ref` | Typed immutable locator |
| `uncertainty_statement_refs` | array |
| `comparison_plan_ref` | Typed immutable locator |
| `candidate_refs` | array |

## VerifierQualificationReport

Schema version: `2.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `2.0.0` |
| `report_id` | string |
| `verifier_ref` | Immutable locator; resolve owner/header/scope at integration |
| `task_family_ref` | Immutable locator; resolve owner/header/scope at integration |
| `labeled_set_ref` | Immutable locator; resolve owner/header/scope at integration |
| `correct_total` | integer |
| `incorrect_total` | integer |
| `false_accepts` | integer |
| `false_rejects` | integer |
| `false_acceptance_upper_bound` | number |
| `false_acceptance_ceiling` | number |
| `includes_corrupted_artifacts` | Exact value: `True` |
| `includes_adversarial_outputs` | Exact value: `True` |
| `family_relation` | SAME_FAMILY / MIXED_FAMILY / DIFFERENT_FAMILY |
| `status` | PASS / FAIL / INCONCLUSIVE |
| `content_hash` | SHA-256 of payload excluding this field |
| `accepted_total` | integer |
| `uncertain_total` | integer |
| `uncertain_accepted` | integer |
| `correct_inconclusive` | integer |
| `incorrect_inconclusive` | integer |
| `bound_method` | Exact value: `ONE_SIDED_CLOPPER_PEARSON` |
| `confidence_level` | number |
| `analysis_ref` | Immutable locator; resolve owner/header/scope at integration |

## AdaptiveEvaluationStep

Schema version: `2.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `2.0.0` |
| `step_id` | string |
| `study_ref` | Immutable locator; resolve owner/header/scope at integration |
| `history_ref` | Immutable locator; resolve owner/header/scope at integration |
| `eligible_task_refs` | array |
| `selected_task_ref` | Immutable locator; resolve owner/header/scope at integration |
| `inclusion_probabilities` | array |
| `evaluated_candidate_refs` | array |
| `acquisition_arm` | ADAPTIVE / UNIFORM_ROTATING |
| `predicted_cost_basis` | OBSERVED_PAST / FROZEN_MODEL |
| `hidden_data_accessed` | Exact value: `False` |
| `content_hash` | SHA-256 of payload excluding this field |
| `frozen_candidate_refs` | array |
| `setting_observation_refs` | array |
| `allocation_rule` | Exact value: `ONE_DRAW_CONDITIONAL_PROBABILITY` |

## ObservableInterventionRecord

Schema version: `2.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `2.0.0` |
| `intervention_id` | string |
| `study_ref` | Immutable locator; resolve owner/header/scope at integration |
| `snapshot_ref` | Immutable locator; resolve owner/header/scope at integration |
| `component_ref` | Immutable locator; resolve owner/header/scope at integration |
| `intervention_type` | CONTEXT_BUNDLE / TOOL_BINDING / ARTIFACT_PRODUCER / COMPUTE_PROFILE / HARNESS_BOUNDARY |
| `validation_mode` | OBSERVED_REEXECUTION / PREDICTIVE_ONLY |
| `snapshot_restored` | boolean |
| `descendants_rerun` | boolean |
| `gold_output_available_to_proposer` | Exact value: `False` |
| `independent_verification_ref` | Typed value or null; owning stage determines applicability |
| `side_effect_check` | PASS / FAIL / UNKNOWN |
| `causal_scope` | BOUNDED / UNSUPPORTED |
| `content_hash` | SHA-256 of payload excluding this field |
| `localization_status` | RANKED / AMBIGUOUS / ABSTAINED |
| `replay_trial_count` | integer |
| `no_op_trial_count` | integer |
| `paired_comparison_ref` | Typed value or null; owning stage determines applicability |

## EvidenceDerivationRecord

Schema version: `2.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `2.0.0` |
| `derivation_id` | string |
| `source_refs` | array |
| `source_trust` | array |
| `least_source_trust` | UNTRUSTED / CLAIMED / VERIFIED |
| `output_ref` | Immutable locator; resolve owner/header/scope at integration |
| `factual_status` | UNVERIFIED / INDEPENDENTLY_VERIFIED |
| `command_authority` | Exact value: `NONE` |
| `independent_verification_ref` | Typed value or null; owning stage determines applicability |
| `invalidation_dependency_refs` | array |
| `content_hash` | SHA-256 of payload excluding this field |
| `dependency_snapshot_ref` | Immutable locator; resolve owner/header/scope at integration |

## DriftEvaluationPlan

Schema version: `2.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `2.0.0` |
| `plan_id` | string |
| `strategy` | FROZEN / PERIODIC / TRIGGERED |
| `time_forward_split_ref` | Immutable locator; resolve owner/header/scope at integration |
| `alarm_metric` | string |
| `trigger_threshold` | number |
| `label_budget` | integer |
| `compute_budget_ms` | integer |
| `exchangeability_assessment` | PASS / FAIL / UNKNOWN |
| `formal_guarantee_claimed` | boolean |
| `fallback_ref` | Immutable locator; resolve owner/header/scope at integration |
| `content_hash` | SHA-256 of payload excluding this field |
| `uncertainty_statement_ref` | Typed value or null; owning stage determines applicability |
| `label_arrival` | IMMEDIATE / DELAYED / BATCHED / MISSING / SELECTIVE |
| `label_update_policy` | ARRIVED_VERIFIED_ONLY / SUSPEND_UNSUPPORTED_UPDATES |
| `applicable_assumptions_status` | JUSTIFIED_WITHIN_SCOPE / FAILED / UNRESOLVED |
| `assumption_evidence_ref` | Typed value or null; owning stage determines applicability |

## ResearchDataRolePlan

Schema version: `1.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `1.0.0` |
| `plan_id` | string |
| `independent_unit` | string |
| `grouping_key` | string |
| `calibration_required` | boolean |
| `memberships` | array |
| `reuse_policy` | Exact value: `STRICT_INDEPENDENT_ROLES` |
| `access_order` | Exact value: `['DEVELOPMENT_SELECTION_FROZEN', 'CALIBRATION_IF_REQUIRED', 'FINAL_TEST']` |
| `final_feedback_to_search` | Exact value: `False` |
| `cohort_floor_manifest_ref` | Immutable locator; resolve owner/header/scope at integration |
| `access_policy_ref` | Immutable locator; resolve owner/header/scope at integration |
| `content_hash` | SHA-256 of payload excluding this field |

## UncertaintyStatement

Schema version: `1.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `1.0.0` |
| `statement_id` | string |
| `target` | MEAN_PERFORMANCE / NEW_OUTCOME_PREDICTION / MARGINAL_ERROR / TIME_AVERAGE_ERROR / CONDITIONAL_CLAIM |
| `method` | EMPIRICAL_ONLY / MEAN_CONFIDENCE_INTERVAL / SPLIT_CONFORMAL / ADAPTIVE_CONFORMAL |
| `population_ref` | Immutable locator; resolve owner/header/scope at integration |
| `independent_unit` | string |
| `data_role_plan_ref` | Typed immutable locator |
| `assumptions_status` | JUSTIFIED_WITHIN_SCOPE / FAILED / UNRESOLVED |
| `assumption_evidence_ref` | Typed value or null; owning stage determines applicability |
| `diagnostic_status` | PASS / FAIL / UNKNOWN |
| `formal_guarantee_claimed` | boolean |
| `label_arrival` | COMPLETE_OFFLINE / IMMEDIATE / DELAYED / BATCHED / MISSING / SELECTIVE |
| `label_process_support_ref` | Typed value or null; owning stage determines applicability |
| `calibration_n` | integer |
| `miscoverage` | Typed value or null; owning stage determines applicability |
| `required_rank` | Typed value or null; owning stage determines applicability |
| `quantile_policy` | FULL_SUPPORT_IF_NEEDED / NOT_APPLICABLE |
| `prediction_set` | FINITE / FULL_SUPPORT / NOT_APPLICABLE |
| `safety_authority` | Exact value: `NONE` |
| `content_hash` | SHA-256 of payload excluding this field |

## EffectiveSettingObservation

Schema version: `1.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `1.0.0` |
| `observation_id` | string |
| `candidate_ref` | Immutable locator; resolve owner/header/scope at integration |
| `effective_configuration_ref` | Immutable locator; resolve owner/header/scope at integration |
| `requested_settings_ref` | Immutable locator; resolve owner/header/scope at integration |
| `accepted_settings_ref` | Typed value or null; owning stage determines applicability |
| `observed_settings_ref` | Typed value or null; owning stage determines applicability |
| `required_setting_names` | array |
| `setting_checks` | array |
| `status` | CONFIRMED / MISMATCH / UNKNOWN |
| `cache_condition` | COLD / WARM / UNKNOWN |
| `execution_order` | integer |
| `accounting_snapshot_ref` | Immutable locator; resolve owner/header/scope at integration |
| `allocation_retained` | Exact value: `True` |
| `hidden_reasoning_required` | Exact value: `False` |
| `content_hash` | SHA-256 of payload excluding this field |

## ResearchComparisonPlan

Schema version: `1.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `1.0.0` |
| `comparison_id` | string |
| `study_kind` | PROFILE_ACQUISITION / HARNESS_FACTORIAL / RECOVERY_REPLAY / TRACE_ACQUISITION / BEHAVIOR_SCREENING / JOINT_ADAPTATION / CONTEXT_TRANSFER / DRIFT_SCHEDULE / WORKFLOW_ALLOCATION / SCENARIO_CALIBRATION / STATE_POISONING |
| `controls` | array |
| `candidate_refs` | array |
| `baseline_strength` | MATURE_GOVERNED / DIAGNOSTIC_UNTUNED |
| `candidate_generation` | FROZEN / DEVELOPMENT_ADAPTIVE |
| `acquisition_sampling` | FROZEN / DEVELOPMENT_ADAPTIVE |
| `selection_role` | Exact value: `DEVELOPMENT` |
| `data_role_plan_ref` | Typed immutable locator |
| `pairing_unit` | string |
| `total_budget_ref` | Immutable locator; resolve owner/header/scope at integration |
| `cost_categories` | array |
| `cost_applicability_ref` | Immutable locator; resolve owner/header/scope at integration |
| `hypothesis_manifest_ref` | Immutable locator; resolve owner/header/scope at integration |
| `repair_units` | array |
| `preservation_units` | array |
| `boundary_units` | array |
| `fresh_confirmation_units` | array |
| `proposal_history_ref` | Typed value or null; owning stage determines applicability |
| `optimizer_harness_ref` | Typed value or null; owning stage determines applicability |
| `target_harness_refs` | array |
| `activation_observation_required` | boolean |
| `profile_trial_budget` | Typed value or null; owning stage determines applicability |
| `source_target_content` | Typed value or null; owning stage determines applicability |
| `assembled_pair` | Typed value or null; owning stage determines applicability |
| `evidence_scope` | DIGITAL / SIM_TO_SIM / EXACT_TARGET_PHYSICAL |
| `target_evidence_ref` | Typed value or null; owning stage determines applicability |
| `graph_manifest_ref` | Typed value or null; owning stage determines applicability |
| `mandatory_verifier_refs` | array |
| `command_authority` | Exact value: `NONE` |
| `protected_fields_mutated` | array |
| `minimum_matched_repeats` | integer |
| `content_hash` | SHA-256 of payload excluding this field |

## StateConsumptionReceipt

Schema version: `1.0.0`.

| Field | Shape |
|---|---|
| `schema_version` | Exact value: `1.0.0` |
| `receipt_id` | string |
| `view_ref` | Immutable locator; resolve owner/header/scope at integration |
| `dependency_snapshot_ref` | Immutable locator; resolve owner/header/scope at integration |
| `dependency_refs` | array |
| `observed_epoch` | integer |
| `current_epoch` | integer |
| `dependencies_eligible` | boolean |
| `decision` | CONSUMED / REJECTED_STALE / REJECTED_INELIGIBLE |
| `serialization_owner_ref` | Immutable locator; resolve owner/header/scope at integration |
| `idempotency_key` | string |
| `command_authority` | Exact value: `NONE` |
| `content_hash` | SHA-256 of payload excluding this field |

## Cross-field checks and implementation limits

The validator rejects candidate-count and paired-candidate mismatches, invalid verifier counts or binomial bounds, non-normalized single-draw probabilities, unsupported causal comparisons, trust escalation, overlapping data-role groups, invalid calibration ranks, unsupported guarantee/label claims, unobserved settings, stale consumption, missing study controls, incomplete profile budgets, reused behavior confirmation, mismatched adaptation pairs and invalid transfer/scenario scope.

Fixture locators intentionally include synthetic external artifacts. Their existence, provenance, signatures, access rights and semantic adequacy are not proven by shape validation. The owning implementation must resolve them and enforce tenant, time, candidate and data-role identity. A passed synthetic epoch ordering model is not a proof of production distributed atomicity.
