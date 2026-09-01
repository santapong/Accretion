"""Tests for the SDD §24.8 release gate (deliberately unmarked).

`§24.8` is a release condition, not an acceptance criterion, so nothing here
claims one. What these tests protect is the gate's ability to *fail*: a release
gate that cannot say no is decoration, and the failure modes below are the ones
that would let a broken release through while printing PASS.

The `capability_policy_bypass` counter is the interesting half. SDD §21 declares
fourteen metrics and implements none, so the counter is derived from
`CapabilityGateway` audit rows rather than telemetry (ADR3-M8-002). These tests
pin what "bypass" means, including the cases that are deliberately *not*
bypasses.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from accretion.contracts import (
    AuthorizationOutcome,
    CapabilityAuthorization,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
    CapabilityRequest,
)

ROOT = Path(__file__).resolve().parents[1]


def load_release_gate():
    """Import scripts/release_gate.py, which is a script rather than a module."""
    spec = importlib.util.spec_from_file_location(
        "accretion_release_gate", ROOT / "scripts" / "release_gate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = load_release_gate()


def result(
    *,
    outcome: AuthorizationOutcome,
    status: CapabilityExecutionStatus,
    policy_id: str = "policy_v1",
    approval_id: str | None = None,
    capability_id: str = "fs.read",
) -> CapabilityExecutionResult:
    return CapabilityExecutionResult(
        request=CapabilityRequest(
            request_id="capreq_release_gate",
            run_id="run_release_gate",
            node_id="run_release_gate:act",
            capability_id=capability_id,
            capability_version="1.0.0",
            arguments={},
            declared_reason="release gate fixture",
        ),
        authorization=CapabilityAuthorization(
            outcome=outcome,
            policy_id=policy_id,
            policy_version="1.0.0",
            reason="release gate fixture",
            approval_id=approval_id,
        ),
        status=status,
    )


# --- what counts as a bypass ------------------------------------------------


def test_a_denied_call_that_executed_is_a_bypass() -> None:
    bypasses = gate.capability_policy_bypasses(
        [
            result(
                outcome=AuthorizationOutcome.DENY,
                status=CapabilityExecutionStatus.SUCCEEDED,
            )
        ]
    )
    assert len(bypasses) == 1
    assert "after DENY" in bypasses[0]


@pytest.mark.parametrize(
    "status",
    [
        CapabilityExecutionStatus.EXECUTING,
        CapabilityExecutionStatus.SUCCEEDED,
        CapabilityExecutionStatus.FAILED,
        CapabilityExecutionStatus.UNKNOWN,
    ],
)
def test_every_status_that_reached_the_backend_counts(
    status: CapabilityExecutionStatus,
) -> None:
    """FAILED and UNKNOWN count too.

    A denied call that ran and then failed still ran, and UNKNOWN means the
    gateway never observed a terminal outcome - which is not evidence that
    nothing happened.
    """
    assert gate.capability_policy_bypasses(
        [result(outcome=AuthorizationOutcome.DENY, status=status)]
    )


def test_an_execution_with_no_recorded_policy_is_a_bypass() -> None:
    bypasses = gate.capability_policy_bypasses(
        [
            result(
                outcome=AuthorizationOutcome.ALLOW,
                status=CapabilityExecutionStatus.SUCCEEDED,
                policy_id="",
            )
        ]
    )
    assert len(bypasses) == 1
    assert "no policy recorded" in bypasses[0]


def test_an_unapproved_call_requiring_approval_is_a_bypass() -> None:
    bypasses = gate.capability_policy_bypasses(
        [
            result(
                outcome=AuthorizationOutcome.REQUIRE_APPROVAL,
                status=CapabilityExecutionStatus.SUCCEEDED,
                approval_id=None,
            )
        ]
    )
    assert len(bypasses) == 1
    assert "approval was required" in bypasses[0]


# --- what deliberately does not count ---------------------------------------


def test_a_denied_call_that_never_executed_is_not_a_bypass() -> None:
    """The normal, correct path: policy said no and nothing ran."""
    assert (
        gate.capability_policy_bypasses(
            [
                result(
                    outcome=AuthorizationOutcome.DENY,
                    status=CapabilityExecutionStatus.DENIED,
                )
            ]
        )
        == []
    )


def test_an_approved_call_and_an_allowed_call_are_not_bypasses() -> None:
    assert (
        gate.capability_policy_bypasses(
            [
                result(
                    outcome=AuthorizationOutcome.ALLOW,
                    status=CapabilityExecutionStatus.SUCCEEDED,
                ),
                result(
                    outcome=AuthorizationOutcome.REQUIRE_APPROVAL,
                    status=CapabilityExecutionStatus.SUCCEEDED,
                    approval_id="approval_1",
                ),
                result(
                    outcome=AuthorizationOutcome.REQUIRE_APPROVAL,
                    status=CapabilityExecutionStatus.REQUIRES_APPROVAL,
                ),
            ]
        )
        == []
    )


def test_bypasses_are_counted_per_row_not_collapsed() -> None:
    """Two bad rows must report as two, so a count can be compared against 0."""
    bypasses = gate.capability_policy_bypasses(
        [
            result(
                outcome=AuthorizationOutcome.DENY,
                status=CapabilityExecutionStatus.SUCCEEDED,
                capability_id="fs.write",
            ),
            result(
                outcome=AuthorizationOutcome.DENY,
                status=CapabilityExecutionStatus.FAILED,
                capability_id="net.fetch",
            ),
        ]
    )
    assert len(bypasses) == 2
    assert any("fs.write" in item for item in bypasses)
    assert any("net.fetch" in item for item in bypasses)


def test_an_empty_audit_is_zero_bypasses() -> None:
    assert gate.capability_policy_bypasses([]) == []


# --- the gate's own structure -----------------------------------------------


def test_the_gate_evaluates_every_condition_section_24_8_names() -> None:
    """A condition dropped from the script is a condition nobody checks."""
    expressions = {
        "all(MUST acceptance criteria pass)",
        "secret_exposure_incidents == 0",
        "capability_policy_bypass == 0",
        "connection_isolation_tests == PASS",
        "v0.1/v0.2 regression suite == PASS",
    }
    source = (ROOT / "scripts" / "release_gate.py").read_text()
    for expression in expressions:
        assert expression in source, f"release gate no longer names: {expression}"

    sdd = (ROOT / "docs" / "sdd" / "Accretion_SDD_v0.3.md").read_text()
    for expression in expressions:
        assert expression in sdd, f"expression drifted from the SDD: {expression}"


def test_every_named_suite_exists() -> None:
    """The gate names suites by path; a rename must fail here, not silently
    shrink the gate to the suites that happen to still exist."""
    named = (
        gate.SECRET_SCAN_SUITES
        + gate.CONNECTION_ISOLATION_SUITES
        + gate.POLICY_BYPASS_SUITES
        + gate.REGRESSION_SUITES
    )
    missing = [item for item in named if not (ROOT / item).exists()]
    assert missing == [], f"release gate names suites that do not exist: {missing}"


def test_a_missing_suite_fails_its_condition_instead_of_passing_vacuously() -> None:
    """The failure mode that matters most: a gate that passes because it ran
    nothing. `check_acceptance.py --stage` had exactly this bug."""
    condition = gate.suite_condition(
        "fixture", "fixture == PASS", ["tests/test_does_not_exist_release_gate.py"]
    )
    assert condition.passed is False
    assert "missing" in condition.detail
    assert condition.missing == ["tests/test_does_not_exist_release_gate.py"]


def test_an_empty_suite_list_cannot_report_pass() -> None:
    """Zero named suites is a configuration error, not a pass."""
    condition = gate.suite_condition("fixture", "fixture == PASS", [])
    assert condition.passed is False
    assert "no suites named" in condition.detail
