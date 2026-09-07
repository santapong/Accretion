#!/usr/bin/env python3
"""Executable design conformance checks, NOT Accretion runtime tests.

Uses synthetic extension payloads and normalized lifecycle projections. Never
invokes an executor, changes authority, consumes approval or accesses hardware.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def canonical(value):
    # Golden examples deliberately restrict numbers to integers and text to ASCII.
    # Production must use the predecessor canonicalizer and its full vectors.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def capped_ttvs(status, pass_ms, horizon_ms):
    if horizon_ms <= 0:
        raise ValueError('Horizon must be positive')
    if status == 'PASS' and pass_ms is not None and 0 <= pass_ms <= horizon_ms:
        return pass_ms
    return horizon_ms


def contract_error(case, kit, catalog):
    """Return first named design-contract violation; no runtime authority implied."""
    name = case['schema']
    instance = case['instance']
    schema = {'$schema': kit['$schema'], '$defs': kit['$defs'],
              '$ref': '#/$defs/' + name}
    if list(Draft202012Validator(schema).iter_errors(instance)):
        return 'SCHEMA'
    if 'content_hash' in instance:
        body = {k: v for k, v in instance.items() if k != 'content_hash'}
        if digest(body) != instance['content_hash']:
            return 'CONTENT_HASH'
    if name == 'EvaluationProtocol':
        if instance['minimum_effect_ms'] > instance['horizon_ms']:
            return 'PROTOCOL_EFFECT'
    if name in ('ComputeDecisionPayload', 'HarnessSelectionPayload'):
        if digest(instance['candidates']) != instance['candidate_set_hash']:
            return 'CANDIDATE_SET_HASH'
        if instance['selected_ref'] is not None and instance['selected_ref'] not in instance['candidates']:
            return 'SELECTED_NOT_CANDIDATE'
    if name == 'ComputeDispatchBinding':
        authoritative = case.get('authoritative')
        if not authoritative:
            return 'MISSING_OWNER_PROJECTION'
        for field, expected in authoritative.items():
            actual = instance[field]
            actual = actual['content_hash'] if isinstance(actual, dict) else actual
            if actual != expected:
                return 'BINDING_MISMATCH'
    if name == 'EffectiveComputeConfiguration':
        authority = case.get('assembly_authority')
        if not authority:
            return 'MISSING_OWNER_PROJECTION'
        for field, cap in authority['caps'].items():
            value = instance['reasoning_limits'][field]
            if cap is not None and (value is None or value > cap):
                return 'ASSEMBLY_LIMIT'
        if any(tool['content_hash'] not in authority['allowed_tool_hashes'] for tool in instance['tool_refs']):
            return 'ASSEMBLY_TOOL'
        if not (set(instance) - {'content_hash'}) <= set(authority['supported_fields']):
            return 'ASSEMBLY_ADAPTER_FIELD'
    if name == 'HarnessLifecycleRecord':
        stage = instance['stage']
        evaluation, promotion = instance['evaluation_ref'], instance['promotion_ref']
        if ((stage == 'PACKAGED' and (evaluation is not None or promotion is not None))
                or (stage == 'EVALUATED' and (evaluation is None or promotion is not None))
                or (stage == 'PROMOTED' and (evaluation is None or promotion is None))
                or (stage == 'REVOKED' and not instance['reason'])):
            return 'HARNESS_LIFECYCLE'
    if name == 'PhysicalFreezePayload':
        chain = case.get('chain', {})
        expected = {'trial_manifest_hash': instance['manifest_ref']['content_hash'],
                    'preflight_trial_hash': instance['trial_ref']['content_hash'],
                    'approval_trial_hash': instance['trial_ref']['content_hash'],
                    'approval_preflight_hash': instance['preflight_ref']['content_hash'],
                    'loaded_manifest_hash': instance['manifest_ref']['content_hash']}
        if chain != expected:
            return 'PHYSICAL_CHAIN'
    if name == 'PhysicalOutcomePayload':
        if instance['verification_status'] == 'PASS' and instance['stage'] != 'VERIFIED':
            return 'PHYSICAL_UNPROVEN_PASS'
        if instance['verification_status'] != 'PASS' and instance['verified_pass_ms'] is not None:
            return 'PHYSICAL_FALSE_PASS_TIME'
        if instance['capped_ttvs_ms'] != capped_ttvs(instance['verification_status'],
                                                   instance['verified_pass_ms'], instance['horizon_ms']):
            return 'PHYSICAL_METRIC'
        missing = any(value is None for value in instance['timing_ms'].values())
        if missing and (instance['measurement_quality'] == 'COMPLETE' or not instance['missing_reason']):
            return 'PHYSICAL_MISSING_TIMING'
        if instance['physical_reexecutions_observed'] > 0 and not instance['incident_refs']:
            return 'PHYSICAL_MISSING_INCIDENT'
        allowed = {
            'PRE_FREEZE': {'PREFLIGHT_REJECTED', 'INELIGIBLE', 'SIMULATION_FAIL', 'INCONCLUSIVE', 'CANCELLED'},
            'FROZEN_UNAPPROVED': {'APPROVAL_DENIED', 'CANCELLED', 'INVALIDATED'},
            'APPROVED_UNARMED': {'APPROVAL_EXPIRED', 'CANCELLED', 'INVALIDATED'},
            'ARMED_NO_EXECUTION': {'LEASE_EXPIRED', 'STOPPED', 'CANCELLED'},
            'EXECUTED_UNVERIFIED': {'VERIFIER_UNAVAILABLE', 'SAFETY_EVIDENCE_MISSING', 'STOPPED', 'INCONCLUSIVE'},
            'VERIFIED': {'VERIFIED'}
        }
        if instance['terminal_reason'] not in allowed[instance['stage']]:
            return 'PHYSICAL_STAGE_REASON'
    if name == 'EventPayload':
        event = catalog.get(instance['event_name'])
        if event is None:
            return 'EVENT_UNKNOWN'
        if (event['subject_contract'] != instance['subject_ref']['contract_type']
                or instance['subject_digest'] != instance['subject_ref']['content_hash']):
            return 'EVENT_SUBJECT'
    if name == 'LabClosure':
        if instance['adapter_promoted'] and (instance['research_closure'] != 'PASS'
                or instance['platform_readiness'] != 'PASS' or instance['critical_incident_open']):
            return 'LAB_PROMOTION'
    return None


def lifecycle(case):
    """Small executable specifications of decisions; not production controllers."""
    state = copy.deepcopy(case['initial'])
    rejected = []
    for action in case['actions']:
        model = case['model']
        if model == 'dispatch':
            if action == 'COMMIT':
                if state['mode'] == 'SHADOW':
                    rejected.append('SHADOW')
                elif state['epoch'] != state['expected_epoch']:
                    rejected.append('STALE_EPOCH'); state['state'] = 'INVALIDATED'
                else:
                    if not state['committed']:
                        state['committed'] = True; state['state'] = 'COMMITTED'
            elif action == 'CRASH_BEFORE_COMMIT':
                # Atomic transaction has committed none of receipt/reservation/outbox.
                assert not state['committed']
            elif action == 'MUTATE':
                state['epoch'] += 1
            elif action == 'REUSE_KEY_CHANGED_PAYLOAD':
                rejected.append('IDEMPOTENCY_CONFLICT')
            elif action in ('DELIVER', 'DELIVER_UNKNOWN'):
                if not state['committed']:
                    rejected.append('NOT_COMMITTED')
                elif state['state'] == 'RECONCILE':
                    rejected.append('UNCERTAIN_INVOCATION')
                elif state['invocations']:
                    rejected.append('DUPLICATE')
                elif state['epoch'] != state['expected_epoch']:
                    rejected.append('STALE_EPOCH'); state['state'] = 'INVALIDATED'
                elif state.get('now_ms', 0) >= state.get('expires_at_ms', 1000):
                    rejected.append('EXPIRED'); state['state'] = 'INVALIDATED'
                else:
                    state['invocations'] += 1
                    state['state'] = 'RECONCILE' if action == 'DELIVER_UNKNOWN' else 'EXECUTED'
            else:
                raise AssertionError('Unknown dispatch action: ' + action)
        elif model == 'recovery':
            if action == 'CLASSIFY':
                state['state'] = ('STOPPED' if state['physical'] or any(state.get(k) for k in ('safety', 'authority', 'quarantined')) else
                                  'PAUSED' if state['conflict'] or state['unknown'] else 'ELIGIBLE')
            elif action == 'EVIDENCE':
                if state['state'] != 'PAUSED':
                    rejected.append('NOT_PAUSED')
                # Evidence acquisition does not change verification or authority.
            elif action == 'HUMAN_RESOLVE':
                state['human_resolution'] = True
            elif action == 'INDEPENDENT_PASS':
                state['independent_pass'] = True
            elif action == 'ACCEPT':
                if state['state'] == 'STOPPED':
                    rejected.append('STOPPED')
                elif not state['human_resolution']:
                    rejected.append('NO_HUMAN_RESOLUTION')
                elif not state['independent_pass']:
                    rejected.append('NO_INDEPENDENT_PASS')
                else:
                    state['state'] = 'SUCCEEDED'
            elif action == 'DISPATCH':
                if state['physical']:
                    rejected.append('PHYSICAL_RETRY')
                elif state['state'] == 'PAUSED':
                    rejected.append('PAUSED')
                elif state['state'] != 'ELIGIBLE':
                    rejected.append('STOPPED')
                else:
                    state['state'] = 'DISPATCHED'
            else:
                raise AssertionError('Unknown recovery action: ' + action)
        elif model == 'arm':
            if action == 'MUTATE':
                state['epoch'] += 1
                if state['consumed']:
                    state['state'] = 'STOP_REQUIRED'; rejected.append('EXISTING_SAFETY_PATH')
                else:
                    state['state'] = 'INVALIDATED'
            elif action == 'ARM':
                if state['consumed']:
                    rejected.append('CONSUMED')
                elif state['epoch'] != state['expected_epoch']:
                    state['state'] = 'INVALIDATED'; rejected.append('STALE_EPOCH')
                elif not state.get('signature_valid', True):
                    state['state'] = 'INVALIDATED'; rejected.append('INVALID_SIGNATURE')
                elif state.get('now_ms', 0) >= state.get('expires_at_ms', 1000):
                    state['state'] = 'INVALIDATED'; rejected.append('EXPIRED')
                else:
                    state['consumed'] = True; state['arms'] += 1; state['state'] = 'ARMED'
            else:
                raise AssertionError('Unknown arm action: ' + action)
        else:
            raise AssertionError('Unknown model: ' + model)
    assert state.get('invocations', 0) <= 1, 'Duplicate invocation'
    assert state.get('arms', 0) <= 1, 'Duplicate approval consumption'
    return state['state'], rejected


def main():
    kit = read('15_CONTRACT_KIT.json')
    Draft202012Validator.check_schema(kit)
    for schema in kit['$defs'].values():
        Draft202012Validator.check_schema(schema)
    event_doc = read('16_EVENT_CATALOG.json')
    catalog = {event['name']: event for event in event_doc['events']}
    assert len(catalog) == len(event_doc['events']), 'Duplicate event ownership'
    fixture_doc = read('17_CONTRACT_LIFECYCLE_FIXTURES.json')
    cases = fixture_doc['cases']
    ids = {case['id'] for case in cases}
    assert len(ids) == len(cases), 'Duplicate fixture ID'
    results = []
    for case in cases:
        cid = case['id']
        if case['kind'] == 'contract':
            actual = contract_error(case, kit, catalog)
            assert actual == case['expected_error'], (cid, actual, case['expected_error'])
            assert (actual is None) == case['valid'], cid
            if 'expected_hard_gate' in case:
                observed = 'FAIL' if case['instance']['physical_reexecutions_observed'] > 0 else 'UNDETERMINED'
                assert observed == case['expected_hard_gate'], cid
        elif case['kind'] == 'metric':
            actual = capped_ttvs(case['status'], case['pass_ms'], case['horizon_ms'])
            assert actual == case['expected_capped_ms'], cid
        elif case['kind'] == 'lifecycle':
            actual = lifecycle(case)
            assert actual == (case['expected_state'], case['expected_rejections']), (cid, actual)
        else:
            raise AssertionError('Unknown fixture kind')
        results.append(cid)

    # Counterexample regression: early failure cannot manufacture latency savings.
    baseline = [capped_ttvs('PASS', 60, 100)] * 10
    candidate = [capped_ttvs('PASS', 60, 100)] * 9 + [capped_ttvs('FAIL', None, 100)]
    assert sum(candidate) > sum(baseline)
    assert (9 * 60 + 1) < sum(baseline)  # The rejected time-to-terminal metric would improve.
    # Parallel work is elapsed wall time, whereas total compute sums work.
    spans = [(0, 20), (5, 25)]
    assert max(end for _, end in spans) - min(start for start, _ in spans) == 25
    assert sum(end - start for start, end in spans) == 40
    gold = fixture_doc['golden_hash']
    assert canonical(gold['input']).decode() == gold['canonical_utf8']
    assert digest(gold['input']) == gold['sha256']

    # Enforce catalog equality with every release's human-readable owner slice.
    seen_events = set()
    for file in sorted(ROOT.glob('*_SDD_v1.*.md')):
        text = file.read_text()
        section = re.search(r'^## 12\..*?(?=^## 13\.)', text, re.M | re.S)[0]
        names = set(re.findall(r'(?m)^[a-z][a-z_]*(?:\.[a-z_]+)+$', section))
        expected = {name for name, event in catalog.items() if event['source_file'] == file.name}
        assert names == expected, (file.name, names ^ expected)
        seen_events |= names
        assert '**Document revision:** 2' in text, file.name
    assert seen_events == set(catalog)
    for event in catalog.values():
        assert event['payload_schema'] in kit['$defs']
        assert event['subject_contract']

    trace = read('18_ACCEPTANCE_TRACEABILITY.json')['criteria']
    source_ids = {}
    for file in ROOT.glob('*.md'):
        for aid, req in re.findall(r'(?m)^- \[ \] \*\*(AC(?:1\d|X)-[A-Z]\d+-\d+)\*\* (.+)$', file.read_text()):
            assert aid not in source_ids, ('Duplicate criterion', aid)
            source_ids[aid] = (file.name, req)
    assert len(trace) == len({row['acceptance_id'] for row in trace}), 'Duplicate trace row'
    assert set(source_ids) == {row['acceptance_id'] for row in trace}, 'Incomplete acceptance coverage'
    for row in trace:
        aid = row['acceptance_id']
        assert source_ids[aid] == (row['source_file'], row['requirement']), aid
        assert row['status'] in ('IMPLEMENTATION_PENDING', 'RESEARCH_PROTOCOL_PENDING'), aid
        assert row['evidence_exists'] is False, 'Design test cannot establish implementation evidence'
        assert row['milestone'] and row['accountable_role'] and row['evidence_target'], aid
        assert set(row['design_fixture_ids']) <= ids, aid
        if row['release'] != 'shared':
            text = (ROOT / row['source_file']).read_text()
            assert re.search(r'^\| ' + re.escape(row['milestone']) + r' \|', text, re.M), aid

    summary = {'result': 'PASS', 'scope': 'DOCUMENT_DESIGN_CONFORMANCE_ONLY',
               'schemas': len(kit['$defs']), 'fixture_cases': len(results),
               'contract_cases': sum(c['kind'] == 'contract' for c in cases),
               'lifecycle_cases': sum(c['kind'] == 'lifecycle' for c in cases),
               'metric_cases': sum(c['kind'] == 'metric' for c in cases),
               'event_names': len(catalog), 'acceptance_rows': len(trace),
               'runtime_integration': 'NOT_RUN', 'research_gates': 'NOT_EVALUATED'}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
