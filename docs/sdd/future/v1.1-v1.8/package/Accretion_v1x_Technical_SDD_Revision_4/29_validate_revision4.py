#!/usr/bin/env python3
"""Revision-4 synthetic design conformance, never runtime/research acceptance.

No executor, model, provider, robot, service or repository mutation is invoked.
The historical revision-2 functions specify inherited payload/lifecycle rules.
Full owner reference resolution, signatures, production canonicalization and
distributed atomicity remain implementation obligations, not fixture results.
"""
import copy
from fractions import Fraction
import hashlib
import importlib.util
import json
from math import ceil, comb, isclose
from pathlib import Path
import re
import sys
from urllib.parse import urlparse, unquote

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('revision2', ROOT / '19_validate_revision2.py')
R2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R2)

CHANGED = {'ResearchStudyPlan', 'VerifierQualificationReport', 'AdaptiveEvaluationStep',
           'ObservableInterventionRecord', 'EvidenceDerivationRecord', 'DriftEvaluationPlan'}
NEW = {'ResearchDataRolePlan', 'UncertaintyStatement', 'EffectiveSettingObservation',
       'StateConsumptionReceipt', 'ResearchComparisonPlan'}
REQUIRED_CONTROLS = {
    'PROFILE_ACQUISITION': {'MATURE_BASELINE','UNIFORM_ROTATING','ADAPTIVE','ALL_THREE_PROFILES'},
    'HARNESS_FACTORIAL': {'MATURE_GENERAL','BOUNDED_SPECIALIST','STATIC_REGIME','ALL_FACTORIAL_CELLS'},
    'RECOVERY_REPLAY': {'FULL_RETRY','NO_OP_REPLAY','RULE_INTERVENTION','PROPOSED_INTERVENTION'},
    'TRACE_ACQUISITION': {'FULL_POOL','UNIFORM_ROTATING','DIVERSITY_CORESET','ALL_FROZEN_CANDIDATES'},
    'BEHAVIOR_SCREENING': {'SCORE_ONLY','FROZEN_DIVERSITY','BEHAVIOR_LINKED','FRESH_CONFIRMATION'},
    'JOINT_ADAPTATION': {'FIXED_BASELINE','HARNESS_ONLY','ADAPTER_ONLY','ALTERNATING'},
    'CONTEXT_TRANSFER': {'TARGET_ONLY','STATIC_PRIOR','TARGET_UPDATED_CONTEXT','INCOMPATIBLE_SOURCE','EMPTY_PRIOR'},
    'DRIFT_SCHEDULE': {'TARGET_ONLY','FROZEN','PERIODIC','TRIGGERED'},
    'WORKFLOW_ALLOCATION': {'FCFS','STATIC_CRITICAL_PATH','DETERMINISTIC_SOLVER','LEARNED_ADVISORY'},
    'SCENARIO_CALIBRATION': {'SIMULATOR_ONLY','TARGET_ONLY','RESIDUAL_CORRECTION','SCENARIO_MODEL'},
    'STATE_POISONING': {'CLEAN','INJECTED','SAME_SESSION','LATER_SESSION','RECOMPUTE_ALL','SELECTIVE_INVALIDATION','UNSAFE_COMMAND_DISABLED_REUSE'},
}
COST_CATEGORIES = {'EXECUTION','VERIFICATION','PROPOSER','ACQUISITION','FAILED_CANDIDATES',
                   'TRAINING','HUMAN','SERVING','HOLDOUT','WAIT_QUOTA','MAINTENANCE'}

def read(name):
    return json.loads((ROOT / name).read_text())

def key(ref):
    return ref['contract_type'], ref['record_id'], ref['content_hash']

def cp_upper(k, n, confidence):
    """One-sided fixed-sample binomial bound; synthetic independent-unit cases."""
    if k == n:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(100):
        value = (low + high) / 2
        cdf = sum(comb(n, i) * value**i * (1-value)**(n-i) for i in range(k+1))
        if cdf > 1-confidence:
            low = value
        else:
            high = value
    return (low + high) / 2

def contract_error(case, kit, catalog):
    # Inherited validator verifies strict schema and payload hash before semantics.
    error = R2.contract_error(case, kit, catalog)
    if error:
        return error
    name, x = case['schema'], case['instance']
    if name == 'ResearchStudyPlan':
        if x['candidate_count'] != len(x['candidate_refs']):
            return 'STUDY_CANDIDATES'
    if name == 'VerifierQualificationReport':
        if (x['false_accepts'] + x['incorrect_inconclusive'] > x['incorrect_total']
            or x['false_rejects'] + x['correct_inconclusive'] > x['correct_total']
            or x['uncertain_accepted'] > x['uncertain_total']
            or x['accepted_total'] != x['correct_total'] - x['false_rejects']
               - x['correct_inconclusive'] + x['false_accepts'] + x['uncertain_accepted']):
            return 'VERIFIER_COUNTS'
        upper = cp_upper(x['false_accepts'], x['incorrect_total'], x['confidence_level'])
        if not isclose(x['false_acceptance_upper_bound'], upper, abs_tol=1e-10):
            return 'VERIFIER_BOUND'
        qualifies = upper <= x['false_acceptance_ceiling']
        if (x['status'] == 'PASS' and not qualifies) or (x['status'] == 'FAIL' and qualifies):
            return 'VERIFIER_STATUS'
    if name == 'AdaptiveEvaluationStep':
        eligible = {key(r) for r in x['eligible_task_refs']}
        probs = [key(p['task_ref']) for p in x['inclusion_probabilities']]
        if key(x['selected_task_ref']) not in eligible or set(probs) != eligible or len(probs) != len(set(probs)):
            return 'ACQUISITION_PROPENSITY'
        values = [p['probability'] for p in x['inclusion_probabilities']]
        if not isclose(sum(values), 1, abs_tol=1e-12):
            return 'ACQUISITION_NORMALIZATION'
        if x['acquisition_arm'] == 'UNIFORM_ROTATING' and max(values)-min(values) > 1e-12:
            return 'ACQUISITION_NOT_UNIFORM'
        if {key(r) for r in x['frozen_candidate_refs']} != {key(r) for r in x['evaluated_candidate_refs']}:
            return 'ACQUISITION_CANDIDATES'
    if name == 'ObservableInterventionRecord' and x['causal_scope'] == 'BOUNDED':
        if not (x['validation_mode'] == 'OBSERVED_REEXECUTION' and x['snapshot_restored']
                and x['descendants_rerun'] and x['independent_verification_ref'] is not None
                and x['side_effect_check'] == 'PASS'):
            return 'CAUSAL_SUPPORT'
        if min(x['replay_trial_count'], x['no_op_trial_count']) < 2 or x['paired_comparison_ref'] is None:
            return 'CAUSAL_COMPARISON'
    if name == 'EvidenceDerivationRecord':
        order = {'UNTRUSTED':0,'CLAIMED':1,'VERIFIED':2}
        if len(x['source_refs']) != len(x['source_trust']) or x['least_source_trust'] != min(x['source_trust'], key=order.get):
            return 'DERIVATION_TRUST'
        if (x['factual_status'] == 'INDEPENDENTLY_VERIFIED') != (x['independent_verification_ref'] is not None):
            return 'DERIVATION_VERIFICATION'
    if name == 'DriftEvaluationPlan' and x['formal_guarantee_claimed']:
        if (x['applicable_assumptions_status'] != 'JUSTIFIED_WITHIN_SCOPE'
            or x['assumption_evidence_ref'] is None or x['uncertainty_statement_ref'] is None):
            return 'DRIFT_GUARANTEE'
    if name == 'ResearchDataRolePlan':
        unit_ids, group_roles = set(), {}
        roles = set()
        for member in x['memberships']:
            unit, group, role = member['unit_id'], member['lineage_group'], member['role']
            if unit in unit_ids or (group in group_roles and group_roles[group] != role):
                return 'DATA_ROLE_LEAKAGE'
            unit_ids.add(unit); group_roles[group] = role; roles.add(role)
        if not {'DEVELOPMENT','FINAL_TEST'} <= roles or (x['calibration_required'] and 'CALIBRATION' not in roles):
            return 'DATA_ROLE_MISSING'
    if name == 'UncertaintyStatement':
        if x['formal_guarantee_claimed'] and (x['assumptions_status'] != 'JUSTIFIED_WITHIN_SCOPE'
            or x['assumption_evidence_ref'] is None or x['method'] == 'EMPIRICAL_ONLY'):
            return 'UNCERTAINTY_ASSUMPTIONS'
        if x['method'] == 'SPLIT_CONFORMAL':
            if x['target'] not in ('MARGINAL_ERROR','NEW_OUTCOME_PREDICTION'):
                return 'UNCERTAINTY_TARGET'
            if x['miscoverage'] is None or x['calibration_n'] < 1:
                return 'CONFORMAL_RANK'
            rank = ceil((x['calibration_n']+1) * (1-Fraction(str(x['miscoverage']))))
            required_set = 'FULL_SUPPORT' if rank > x['calibration_n'] else 'FINITE'
            if x['required_rank'] != rank or x['prediction_set'] != required_set or x['quantile_policy'] != 'FULL_SUPPORT_IF_NEEDED':
                return 'CONFORMAL_RANK'
        elif x['required_rank'] is not None or x['quantile_policy'] != 'NOT_APPLICABLE' or x['prediction_set'] != 'NOT_APPLICABLE':
            return 'CONFORMAL_RANK'
        if x['method'] == 'ADAPTIVE_CONFORMAL' and x['target'] != 'TIME_AVERAGE_ERROR':
            return 'UNCERTAINTY_TARGET'
        if x['method'] == 'MEAN_CONFIDENCE_INTERVAL' and x['target'] != 'MEAN_PERFORMANCE':
            return 'UNCERTAINTY_TARGET'
        if x['formal_guarantee_claimed'] and x['label_arrival'] not in ('IMMEDIATE','COMPLETE_OFFLINE') and x['label_process_support_ref'] is None:
            return 'UNCERTAINTY_LABEL_PROCESS'
    if name == 'EffectiveSettingObservation':
        checks = {item['name']:item for item in x['setting_checks']}
        if len(checks) != len(x['setting_checks']) or not set(x['required_setting_names']) <= set(checks):
            return 'SETTING_FIELDS'
        if x['status'] == 'CONFIRMED':
            if x['accepted_settings_ref'] is None or x['observed_settings_ref'] is None:
                return 'SETTING_UNPROVEN'
            for name in x['required_setting_names']:
                item = checks[name]
                if not (item['supported'] and item['accepted'] and item['observed'] == 'APPLIED' and item['evidence_ref'] is not None):
                    return 'SETTING_UNPROVEN'
    if name == 'StateConsumptionReceipt':
        stale = x['observed_epoch'] != x['current_epoch']
        if (x['decision'] == 'CONSUMED' and (stale or not x['dependencies_eligible'])
            or x['decision'] == 'REJECTED_STALE' and not stale
            or x['decision'] == 'REJECTED_INELIGIBLE' and x['dependencies_eligible']):
            return 'STATE_CONSUMPTION'
    if name == 'ResearchComparisonPlan':
        kind = x['study_kind']
        if not REQUIRED_CONTROLS[kind] <= set(x['controls']):
            return 'COMPARISON_CONTROLS'
        if x['baseline_strength'] != 'MATURE_GOVERNED':
            return 'COMPARISON_BASELINE'
        if set(x['cost_categories']) != COST_CATEGORIES:
            return 'COMPARISON_COST'
        if kind in ('PROFILE_ACQUISITION','HARNESS_FACTORIAL','TRACE_ACQUISITION') and x['candidate_generation'] != 'FROZEN':
            return 'COMPARISON_STAGE'
        if kind in ('PROFILE_ACQUISITION','HARNESS_FACTORIAL') and not x['activation_observation_required']:
            return 'SETTING_UNPROVEN'
        if kind == 'PROFILE_ACQUISITION':
            b = x['profile_trial_budget']
            if b is None or len(x['candidate_refs']) != 3 or len(b['arms']) != 2 or {a['name'] for a in b['arms']} != {'ADAPTIVE','UNIFORM_ROTATING'}:
                return 'PROFILE_BUDGET'
            used = [3*a['tasks']*a['repeats'] for a in b['arms']]
            if any(total > arm['profile_trial_cap'] for total,arm in zip(used,b['arms'])):
                return 'PROFILE_BUDGET'
            expected = sum(used) + 3*b['holdout_tasks']*b['holdout_repeats'] + b['qualification_trials'] + b['extra_trials']
            if expected != b['declared_total_trials']:
                return 'PROFILE_BUDGET'
        elif x['profile_trial_budget'] is not None:
            return 'PROFILE_BUDGET'
        if kind == 'RECOVERY_REPLAY' and x['minimum_matched_repeats'] < 2:
            return 'CAUSAL_COMPARISON'
        if kind == 'BEHAVIOR_SCREENING':
            sets = [set(x[f]) for f in ('repair_units','preservation_units','boundary_units','fresh_confirmation_units')]
            if not all(sets) or any(a & b for i,a in enumerate(sets) for b in sets[i+1:]) or x['proposal_history_ref'] is None:
                return 'BEHAVIOR_SETS'
        if kind == 'JOINT_ADAPTATION':
            pair = x['assembled_pair']
            if pair is None or pair['rollout_pair_hash'] != pair['training_data_pair_hash']:
                return 'JOINT_PAIR'
        if kind == 'CONTEXT_TRANSFER':
            content = x['source_target_content']
            if (content is None or set(content['source_bound_fields']) & set(content['transferable_fields'])
                or content['source_snapshot_ref'] == content['target_descendant_ref']
                or content['prior_status'] == 'EMPTY' and content['transferable_fields']):
                return 'TRANSFER_CONTENT'
        if kind == 'WORKFLOW_ALLOCATION' and (x['evidence_scope'] != 'SIM_TO_SIM' or x['graph_manifest_ref'] is None or not x['mandatory_verifier_refs']):
            return 'WORKFLOW_SCOPE'
        if kind == 'SCENARIO_CALIBRATION' and (x['evidence_scope'] not in ('SIM_TO_SIM','EXACT_TARGET_PHYSICAL') or x['target_evidence_ref'] is None or x['pairing_unit'] != 'SCENE_CONFIGURATION'):
            return 'SCENARIO_SCOPE'
    return None

def lifecycle(case):
    model = case['model']
    if model not in ('research','research_v4','state_consumption'):
        return R2.lifecycle(case)
    x = copy.deepcopy(case['initial']); rejected = []
    for action in case['actions']:
        if model in ('research','research_v4'):
            if action == 'FREEZE':
                if x['state'] != 'DRAFT': rejected.append('ALREADY_FROZEN')
                else: x['state'] = 'FROZEN'
            elif action == 'DEV_OBSERVE':
                if x['state'] != 'FROZEN': rejected.append('NOT_FROZEN')
            elif action == 'FREEZE_SELECTION':
                if x['state'] != 'FROZEN': rejected.append('NOT_FROZEN')
                else: x.update(selected=True,state='SELECTED')
            elif action == 'CALIBRATE':
                if x['state'] != 'SELECTED': rejected.append('SELECTION_NOT_FROZEN')
                else: x.update(calibrated=True,state='CALIBRATED')
            elif action == 'CHANGE_SELECTION':
                if x['state'] in ('CALIBRATED','CLOSED'): rejected.append('NEW_PROTOCOL_REQUIRED')
                else: x.update(selected=False,state='FROZEN')
            elif action == 'FINAL_HOLDOUT':
                if x['final_holdout_uses'] >= 1: rejected.append('HOLDOUT_REUSE')
                elif x['state'] == 'DRAFT': rejected.append('NOT_FROZEN')
                elif model == 'research_v4' and not x['selected']: rejected.append('SELECTION_NOT_FROZEN')
                elif model == 'research_v4' and x['calibration_required'] and not x['calibrated']: rejected.append('CALIBRATION_REQUIRED')
                else: x['final_holdout_uses'] += 1; x['state'] = 'CLOSED'
            else: raise AssertionError(action)
        else:
            if action == 'REVOKE':
                x['epoch'] += 1
                x['state'] = 'DESCENDANTS_INVALIDATED' if x['consumed'] else 'INVALIDATED'
            elif action == 'CONSUME':
                if x['observed_epoch'] != x['epoch']:
                    rejected.append('STALE_EPOCH')
                    if not x['consumed']: x['state'] = 'REJECTED_STALE'
                elif not x['consumed']:
                    x.update(consumed=True,state='CONSUMED'); x['uses'] += 1
            elif action == 'CRASH_BEFORE_COMMIT': assert not x['consumed']
            else: raise AssertionError(action)
    assert x.get('final_holdout_uses',0) <= 1 and x.get('uses',0) <= 1
    return x['state'], rejected

def event_names(file):
    number = 20 if file.name.startswith('03_') else 12
    match = re.search(rf'^## {number}\. .*?(?=^## {number+1}\.)', file.read_text(), re.M | re.S)
    assert match, file.name
    return set(re.findall(r'(?m)^[a-z][a-z_]*(?:\.[a-z_]+)+$', match[0]))

def main():
    kit = read('15_CONTRACT_KIT.json')
    Draft202012Validator.check_schema(kit)
    for item in kit['$defs'].values(): Draft202012Validator.check_schema(item)
    assert 'document revision 4' in kit['title']
    events = read('16_EVENT_CATALOG.json'); fixtures = read('17_CONTRACT_LIFECYCLE_FIXTURES.json'); trace = read('18_ACCEPTANCE_TRACEABILITY.json')
    assert events['document_revision'] == fixtures['document_revision'] == trace['document_revision'] == 4
    catalog = {e['name']:e for e in events['events']}
    assert len(catalog) == len(events['events'])
    cases = fixtures['cases']; ids = {c['id'] for c in cases}
    assert len(ids) == len(cases)
    for case in cases:
        if case['kind'] == 'contract':
            actual = contract_error(case,kit,catalog)
            assert actual == case['expected_error'], (case['id'],actual,case['expected_error'])
            assert (actual is None) == case['valid'], case['id']
            if 'expected_hard_gate' in case:
                actual_gate = 'FAIL' if case['instance']['physical_reexecutions_observed'] > 0 else 'UNDETERMINED'
                assert actual_gate == case['expected_hard_gate']
        elif case['kind'] == 'metric':
            assert R2.capped_ttvs(case['status'],case['pass_ms'],case['horizon_ms']) == case['expected_capped_ms']
        elif case['kind'] == 'lifecycle':
            assert lifecycle(case) == (case['expected_state'],case['expected_rejections']), case['id']
        else: raise AssertionError(case['kind'])
    for name in NEW | CHANGED:
        for valid in (True,False):
            assert any(c.get('schema') == name and c['valid'] == valid for c in cases), (name,valid)
    for kind in REQUIRED_CONTROLS:
        assert any(c.get('schema') == 'ResearchComparisonPlan' and c['valid'] and c['instance']['study_kind'] == kind for c in cases)
    gold = fixtures['golden_hash']
    assert R2.canonical(gold['input']).decode() == gold['canonical_utf8'] and R2.digest(gold['input']) == gold['sha256']
    assert 9*R2.capped_ttvs('PASS',60,100) + R2.capped_ttvs('FAIL',None,100) > 10*60
    assert isclose(cp_upper(0,15,.95), 1-.05**(1/15), abs_tol=1e-12)
    assert Fraction(15,16) < Fraction(95,100)

    sources = sorted(ROOT.glob('*_SDD_v1.*.md')) + [ROOT/'03_SHARED_RESEARCH_EVALUATION_PROTOCOL.md']
    seen = set()
    for file in sources:
        names = event_names(file)
        assert names == {n for n,e in catalog.items() if e['source_file'] == file.name}, file.name
        assert '**Document revision:** 4' in file.read_text()
        seen |= names
    assert seen == set(catalog)
    for event in catalog.values():
        assert event['payload_schema'] in kit['$defs'] and event['subject_contract']
        if event['subject_contract'] in NEW: assert event['authority'] == 'OBSERVATION_ONLY'
    source_ids = {}
    for file in ROOT.glob('*.md'):
        for aid,req in re.findall(r'(?m)^- \[ \] \*\*(AC(?:1\d|X)-[A-Z]\d+-\d+)\*\* (.+)$',file.read_text()):
            assert aid not in source_ids, aid
            source_ids[aid] = (file.name,req)
    rows = trace['criteria']
    assert len(rows) == len({r['acceptance_id'] for r in rows})
    assert set(source_ids) == {r['acceptance_id'] for r in rows}
    for row in rows:
        aid = row['acceptance_id']
        assert source_ids[aid] == (row['source_file'],row['requirement']), aid
        assert row['status'] in ('IMPLEMENTATION_PENDING','RESEARCH_PROTOCOL_PENDING') and row['evidence_exists'] is False
        assert row['accountable_role'] and row['evidence_target'] and row['milestone']
        assert set(row['design_fixture_ids']) <= ids, aid
        if row['release'] != 'shared':
            assert re.search(r'^\| '+re.escape(row['milestone'])+r' \|',(ROOT/row['source_file']).read_text(),re.M), aid
    links = 0
    for file in ROOT.glob('*.md'):
        for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',file.read_text()):
            target = target.strip('<>')
            if urlparse(target).scheme or target.startswith('#'): continue
            path = unquote(target.split('#',1)[0])
            assert (file.parent/path).exists(), (file.name,path)
            links += 1
    assert 'NOT_READY_TO_EXECUTE' in (ROOT/'24_V1_1_PILOT_READINESS_AND_PROTOCOL.md').read_text()
    summary = dict(result='PASS',scope='DOCUMENT_DESIGN_CONFORMANCE_ONLY',schemas=len(kit['$defs']),fixture_cases=len(cases),contract_cases=sum(c['kind']=='contract' for c in cases),lifecycle_cases=sum(c['kind']=='lifecycle' for c in cases),metric_cases=sum(c['kind']=='metric' for c in cases),event_names=len(catalog),acceptance_rows=len(rows),release_sdds=8,local_links_checked=links,runtime_integration='NOT_RUN',research_gates='NOT_EVALUATED',independent_design_review='PENDING',v1_1_pilot='NOT_READY_TO_EXECUTE')
    print(json.dumps(summary,indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
