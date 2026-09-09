"""Ten-check assembly fixtures; no operational preflight/conformance authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from v05_sdk_fixtures import Harness

from accretion.contracts.canonical import canonical_json
from accretion.robotics.host.preflight import (
    PreflightBuilder,
    PreflightName,
    PreflightPlan,
    PreflightProof,
    PreflightRefusal,
)
from accretion.robotics.host.worker import PlannedEpisodePins
from accretion.robotics.testing.fault_adapter import MemoryArtifacts


class FixtureChecker:
    def __init__(self, name, reference):
        self.name, self.reference = name, reference
        self.mutate = lambda proof: proof
        self.seen = []

    def evaluate(self, plan):
        self.seen.append(canonical_json(plan))
        return self.mutate(
            PreflightProof(
                name=self.name,
                plan_digest=plan.digest,
                observed_at=plan.now,
                valid_until=plan.valid_until,
                source_refs=(self.reference,),
                reason_code="SYNTHETIC_CHECKER_ONLY",
            )
        )


@pytest.fixture
def case():
    harness = Harness()
    now = datetime.now(UTC)
    plan = PreflightPlan(
        workspace_id=harness.pins.workspace_id,
        project_id=harness.pins.project_id,
        run_id="test-only-preflight-run",
        episode=PlannedEpisodePins.from_full(harness.episode),
        dependencies=harness.dependencies,
        now=now,
        valid_until=now + timedelta(minutes=1),
    )
    artifacts = MemoryArtifacts()
    ref = artifacts.put(b'{"fixture":"NO_PRODUCTION_AUTHORITY"}', media_type="application/json")
    checkers = {name: FixtureChecker(name, ref) for name in PreflightName}
    return plan, artifacts, checkers, ref


def test_exact_ten_checks_verify_bytes_once_without_granting_activation(case):
    plan, artifacts, checkers, _ = case
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    assert result.all_checks_satisfied and not result.activation_eligible
    assert result.scope == "PREFLIGHT_FOUNDATION_ONLY"
    assert tuple(finding.name for finding in result.findings) == tuple(PreflightName)
    assert artifacts.read_calls == 1
    assert result.plan_bytes == canonical_json(plan)
    assert all(finding.proof_bytes for finding in result.findings)


@pytest.mark.parametrize("name", list(PreflightName))
def test_each_missing_named_checker_refuses_without_default_pass(case, name):
    plan, artifacts, checkers, _ = case
    del checkers[name]
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    finding = next(f for f in result.findings if f.name is name)
    assert not finding.satisfied and finding.reason_code == "REQUIRED_CHECK_UNAVAILABLE"
    assert not result.all_checks_satisfied and len(result.findings) == 10


@pytest.mark.parametrize("name", list(PreflightName))
def test_every_checker_may_refuse_missing_actual_trust(case, name):
    plan, artifacts, checkers, _ = case

    def refuse(_):
        raise PreflightRefusal("EXACT_AUTHORITY_UNAVAILABLE")

    checkers[name].evaluate = refuse
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    finding = next(f for f in result.findings if f.name is name)
    assert not finding.satisfied and finding.reason_code == "EXACT_AUTHORITY_UNAVAILABLE"


@pytest.mark.parametrize("change", ["name", "digest", "expired", "future", "extends_plan"])
def test_proof_is_bound_to_complete_plan_and_bounded_freshness(case, change):
    plan, artifacts, checkers, _ = case
    updates = {
        "name": {"name": PreflightName.CONFORMANCE},
        "digest": {"plan_digest": "0" * 64},
        "expired": {"valid_until": plan.now},
        "future": {"observed_at": plan.now + timedelta(seconds=1)},
        "extends_plan": {"valid_until": plan.valid_until + timedelta(seconds=1)},
    }[change]
    checkers[PreflightName.CONTRACT_HASHES].mutate = lambda proof: proof.model_copy(update=updates)
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    assert not result.findings[0].satisfied and not result.all_checks_satisfied


@pytest.mark.parametrize("value", [True, {"passed": True}, None, "PASS"])
def test_pass_labels_and_booleans_are_not_evidence(case, value):
    plan, artifacts, checkers, _ = case
    checkers[PreflightName.CONTRACT_HASHES].evaluate = lambda _: value
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    assert result.findings[0].reason_code == "INVALID_CHECK_RESULT"
    assert not result.all_checks_satisfied


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_bad_source_bytes_fail_and_are_not_retried_or_refunded(case, failure):
    plan, artifacts, checkers, reference = case
    if failure == "missing":
        del artifacts.blobs[reference.digest]
    else:
        artifacts.blobs[reference.digest] = b"x" * reference.size_bytes
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    assert not any(f.satisfied for f in result.findings)
    assert artifacts.read_calls == 1
    assert result.findings[-1].reason_code == "PREFLIGHT_ARTIFACT_PREVIOUSLY_FAILED"


def test_metadata_substitution_under_same_digest_is_refused(case):
    plan, artifacts, checkers, _ = case
    checker = checkers[PreflightName.CONFORMANCE]
    checker.reference = checker.reference.model_copy(update={"retention_class": "PROJECT"})
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    assert result.findings[1].reason_code == "PREFLIGHT_ARTIFACT_METADATA_CONFLICT"


def test_byte_budget_refuses_before_reader_allocation(case):
    plan, artifacts, checkers, ref = case
    result = PreflightBuilder(checkers, max_source_bytes=ref.size_bytes - 1).assess(
        plan, source=artifacts
    )
    assert not result.all_checks_satisfied and artifacts.read_calls == 0
    assert all(f.reason_code == "PREFLIGHT_EVIDENCE_BUDGET_EXHAUSTED" for f in result.findings)


def test_failed_artifact_still_consumes_artifact_budget(case):
    plan, artifacts, checkers, ref = case
    del artifacts.blobs[ref.digest]
    other = artifacts.put(b"another", media_type="application/json")
    checkers[PreflightName.CONFORMANCE].reference = other
    result = PreflightBuilder(checkers, max_source_artifacts=1).assess(plan, source=artifacts)
    assert artifacts.read_calls == 1
    assert result.findings[1].reason_code == "PREFLIGHT_EVIDENCE_BUDGET_EXHAUSTED"


def test_plan_copies_do_not_allow_one_checker_to_poison_later_checks(case):
    plan, artifacts, checkers, _ = case
    original = checkers[PreflightName.CONTRACT_HASHES].evaluate

    def mutate(received):
        received.dependencies.world_digest = "0" * 64
        return original(received)

    checkers[PreflightName.CONTRACT_HASHES].evaluate = mutate
    result = PreflightBuilder(checkers).assess(plan, source=artifacts)
    assert not result.findings[0].satisfied
    assert all(f.satisfied for f in result.findings[1:])
    assert result.plan_bytes == canonical_json(plan)


def test_checker_configuration_mapping_is_snapshotted_and_errors_are_sanitized(case):
    plan, artifacts, checkers, _ = case
    builder = PreflightBuilder(checkers)
    checkers.clear()
    assert builder.assess(plan, source=artifacts).all_checks_satisfied
    _, _, fresh, _ = case

    class Broken:
        def evaluate(self, plan):
            raise RuntimeError("sensitive example path must not enter assessment")

    result = PreflightBuilder({PreflightName.OBSERVATIONS: Broken()}).assess(plan, source=artifacts)
    finding = next(f for f in result.findings if f.name is PreflightName.OBSERVATIONS)
    assert finding.reason_code == "PREFLIGHT_CHECK_FAILED" and finding.proof_bytes is None


@pytest.mark.parametrize("name,value", [("max_source_bytes", True), ("max_source_artifacts", 0)])
def test_budget_configuration_is_strict(case, name, value):
    with pytest.raises(ValueError):
        PreflightBuilder(case[2], **{name: value})
