#!/usr/bin/env python3
"""Revision-3 design conformance checks; never invokes an Accretion executor.

The fixtures are synthetic specifications. A PASS here does not establish runtime
integration, research success, release readiness, deployment, training, paid API
authority, GitHub mutation, or physical-execution authority.
"""
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("revision2_checks", ROOT / "19_validate_revision2.py")
R2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R2)

NEW_SCHEMAS = {
    "ResearchStudyPlan",
    "VerifierQualificationReport",
    "AdaptiveEvaluationStep",
    "ObservableInterventionRecord",
    "EvidenceDerivationRecord",
    "DriftEvaluationPlan",
}


def read(name):
    return json.loads((ROOT / name).read_text())


def ref_key(ref):
    return ref["contract_type"], ref["record_id"], ref["content_hash"]


def revision3_contract_error(case, kit, catalog):
    name = case["schema"]
    if name not in NEW_SCHEMAS:
        return R2.contract_error(case, kit, catalog)
    instance = case["instance"]
    schema = {"$schema": kit["$schema"], "$defs": kit["$defs"], "$ref": "#/$defs/" + name}
    if list(Draft202012Validator(schema).iter_errors(instance)):
        return "SCHEMA"
    if "content_hash" in instance:
        body = {key: value for key, value in instance.items() if key != "content_hash"}
        if R2.digest(body) != instance["content_hash"]:
            return "CONTENT_HASH"

    if name == "VerifierQualificationReport":
        if (instance["false_accepts"] > instance["incorrect_total"]
                or instance["false_rejects"] > instance["correct_total"]):
            return "VERIFIER_COUNTS"
        qualifies = instance["false_acceptance_upper_bound"] <= instance["false_acceptance_ceiling"]
        if instance["status"] == "PASS" and not qualifies:
            return "VERIFIER_STATUS"
        if instance["status"] == "FAIL" and qualifies:
            return "VERIFIER_STATUS"

    if name == "AdaptiveEvaluationStep":
        eligible = {ref_key(ref) for ref in instance["eligible_task_refs"]}
        selected = ref_key(instance["selected_task_ref"])
        probabilities = [ref_key(row["task_ref"]) for row in instance["inclusion_probabilities"]]
        if selected not in eligible or set(probabilities) != eligible or len(probabilities) != len(set(probabilities)):
            return "ACQUISITION_PROPENSITY"
        if instance["acquisition_arm"] == "UNIFORM_ROTATING":
            values = [row["probability"] for row in instance["inclusion_probabilities"]]
            if max(values) - min(values) > 1e-12:
                return "ACQUISITION_NOT_UNIFORM"

    if name == "ObservableInterventionRecord" and instance["causal_scope"] == "BOUNDED":
        supported = (
            instance["validation_mode"] == "DETERMINISTIC_REEXECUTION"
            and instance["snapshot_restored"]
            and instance["descendants_rerun"]
            and instance["independent_verification_ref"] is not None
            and instance["side_effect_check"] == "PASS"
        )
        if not supported:
            return "CAUSAL_SUPPORT"

    if name == "EvidenceDerivationRecord":
        if len(instance["source_refs"]) != len(instance["source_trust"]):
            return "DERIVATION_TRUST"
        order = {"UNTRUSTED": 0, "CLAIMED": 1, "VERIFIED": 2}
        expected = min(instance["source_trust"], key=order.get)
        if instance["least_source_trust"] != expected:
            return "DERIVATION_TRUST"
        if ((instance["factual_status"] == "INDEPENDENTLY_VERIFIED")
                != (instance["independent_verification_ref"] is not None)):
            return "DERIVATION_VERIFICATION"

    if name == "DriftEvaluationPlan":
        if instance["formal_guarantee_claimed"] and instance["exchangeability_assessment"] != "PASS":
            return "DRIFT_GUARANTEE"
    return None


def revision3_lifecycle(case):
    if case["model"] != "research":
        return R2.lifecycle(case)
    state = copy.deepcopy(case["initial"])
    rejected = []
    for action in case["actions"]:
        if action == "FREEZE":
            if state["state"] != "DRAFT":
                rejected.append("ALREADY_FROZEN")
            else:
                state["state"] = "FROZEN"
        elif action == "DEV_OBSERVE":
            if state["state"] != "FROZEN":
                rejected.append("NOT_FROZEN")
        elif action == "FINAL_HOLDOUT":
            if state["state"] not in ("FROZEN", "CLOSED"):
                rejected.append("NOT_FROZEN")
            elif state["final_holdout_uses"] >= 1:
                rejected.append("HOLDOUT_REUSE")
            else:
                state["final_holdout_uses"] += 1
                state["state"] = "CLOSED"
        else:
            raise AssertionError("Unknown research action: " + action)
    assert state["final_holdout_uses"] <= 1
    return state["state"], rejected


def event_names_in(file):
    text = file.read_text()
    if file.name == "03_SHARED_RESEARCH_EVALUATION_PROTOCOL.md":
        section = re.search(r"^## 20\. Shared research events.*?(?=^## 21\.)", text, re.M | re.S)
    else:
        section = re.search(r"^## 12\..*?(?=^## 13\.)", text, re.M | re.S)
    assert section is not None, file.name
    return set(re.findall(r"(?m)^[a-z][a-z_]*(?:\.[a-z_]+)+$", section[0]))


def main():
    kit = read("15_CONTRACT_KIT.json")
    Draft202012Validator.check_schema(kit)
    for schema in kit["$defs"].values():
        Draft202012Validator.check_schema(schema)
    assert "document revision 3" in kit["title"]

    event_doc = read("16_EVENT_CATALOG.json")
    fixture_doc = read("17_CONTRACT_LIFECYCLE_FIXTURES.json")
    trace_doc = read("18_ACCEPTANCE_TRACEABILITY.json")
    assert event_doc["document_revision"] == 3
    assert fixture_doc["document_revision"] == 3
    assert trace_doc["document_revision"] == 3

    catalog = {event["name"]: event for event in event_doc["events"]}
    assert len(catalog) == len(event_doc["events"]), "Duplicate event ownership"
    cases = fixture_doc["cases"]
    ids = {case["id"] for case in cases}
    assert len(ids) == len(cases), "Duplicate fixture ID"

    results = []
    for case in cases:
        cid = case["id"]
        if case["kind"] == "contract":
            actual = revision3_contract_error(case, kit, catalog)
            assert actual == case["expected_error"], (cid, actual, case["expected_error"])
            assert (actual is None) == case["valid"], cid
            if "expected_hard_gate" in case:
                observed = "FAIL" if case["instance"]["physical_reexecutions_observed"] > 0 else "UNDETERMINED"
                assert observed == case["expected_hard_gate"], cid
        elif case["kind"] == "metric":
            actual = R2.capped_ttvs(case["status"], case["pass_ms"], case["horizon_ms"])
            assert actual == case["expected_capped_ms"], cid
        elif case["kind"] == "lifecycle":
            assert revision3_lifecycle(case) == (case["expected_state"], case["expected_rejections"]), cid
        else:
            raise AssertionError("Unknown fixture kind: " + case["kind"])
        results.append(cid)

    valid_new = {case["schema"] for case in cases if case.get("schema") in NEW_SCHEMAS and case["valid"]}
    invalid_new = {case["schema"] for case in cases if case.get("schema") in NEW_SCHEMAS and not case["valid"]}
    assert valid_new == NEW_SCHEMAS and invalid_new == NEW_SCHEMAS

    gold = fixture_doc["golden_hash"]
    assert R2.canonical(gold["input"]).decode() == gold["canonical_utf8"]
    assert R2.digest(gold["input"]) == gold["sha256"]
    baseline = [R2.capped_ttvs("PASS", 60, 100)] * 10
    candidate = [R2.capped_ttvs("PASS", 60, 100)] * 9 + [R2.capped_ttvs("FAIL", None, 100)]
    assert sum(candidate) > sum(baseline)

    event_sources = sorted(ROOT.glob("*_SDD_v1.*.md")) + [ROOT / "03_SHARED_RESEARCH_EVALUATION_PROTOCOL.md"]
    seen_events = set()
    for file in event_sources:
        names = event_names_in(file)
        expected = {name for name, event in catalog.items() if event["source_file"] == file.name}
        assert names == expected, (file.name, names ^ expected)
        seen_events |= names
        if "_SDD_v1." in file.name:
            assert "**Document revision:** 3" in file.read_text(), file.name
    assert seen_events == set(catalog)
    for event in catalog.values():
        assert event["payload_schema"] in kit["$defs"]
        assert event["subject_contract"]

    source_ids = {}
    for file in ROOT.glob("*.md"):
        for aid, requirement in re.findall(
                r"(?m)^- \[ \] \*\*(AC(?:1\d|X)-[A-Z]\d+-\d+)\*\* (.+)$", file.read_text()):
            assert aid not in source_ids, ("Duplicate criterion", aid)
            source_ids[aid] = (file.name, requirement)
    trace = trace_doc["criteria"]
    assert len(trace) == len({row["acceptance_id"] for row in trace}), "Duplicate trace row"
    assert set(source_ids) == {row["acceptance_id"] for row in trace}, "Incomplete acceptance coverage"
    for row in trace:
        aid = row["acceptance_id"]
        assert source_ids[aid] == (row["source_file"], row["requirement"]), aid
        assert row["status"] in ("IMPLEMENTATION_PENDING", "RESEARCH_PROTOCOL_PENDING"), aid
        assert row["evidence_exists"] is False, aid
        assert row["milestone"] and row["accountable_role"] and row["evidence_target"], aid
        assert set(row["design_fixture_ids"]) <= ids, aid
        if row["release"] != "shared":
            source = (ROOT / row["source_file"]).read_text()
            assert re.search(r"^\| " + re.escape(row["milestone"]) + r" \|", source, re.M), aid

    for release in range(1, 9):
        file = ROOT / f"{release + 3:02d}_Accretion_SDD_v1.{release}.0.md"
        text = file.read_text()
        assert "Revision-3" in text, file.name
    assert "NOT_READY_TO_EXECUTE" in (ROOT / "24_V1_1_PILOT_READINESS_AND_PROTOCOL.md").read_text()

    summary = {
        "result": "PASS",
        "scope": "DOCUMENT_DESIGN_CONFORMANCE_ONLY",
        "schemas": len(kit["$defs"]),
        "fixture_cases": len(results),
        "contract_cases": sum(case["kind"] == "contract" for case in cases),
        "lifecycle_cases": sum(case["kind"] == "lifecycle" for case in cases),
        "metric_cases": sum(case["kind"] == "metric" for case in cases),
        "event_names": len(catalog),
        "acceptance_rows": len(trace),
        "release_sdds": 8,
        "runtime_integration": "NOT_RUN",
        "research_gates": "NOT_EVALUATED",
        "v1_1_pilot": "NOT_READY_TO_EXECUTE",
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
