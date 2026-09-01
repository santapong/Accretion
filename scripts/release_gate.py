"""Evaluate the SDD §24.8 release gate and print a verdict per condition.

    release_v0_3 =
        all(MUST acceptance criteria pass)
        AND secret_exposure_incidents == 0
        AND capability_policy_bypass == 0
        AND connection_isolation_tests == PASS
        AND v0.1/v0.2 regression suite == PASS

Until M8 the gate was prose: `check_acceptance.py` covered the first line and the
other four were satisfied by the suite existing, with nothing that could report
them individually. This script makes each condition separately executable and
separately failable, so "the release gate passed" is a claim about five checks
rather than about one.

Two of the conditions name counters that have no telemetry behind them (SDD §21
declares fourteen metrics; none is implemented, and building them would drag
OpenTelemetry into the secret-scan surface list). They are therefore derived
here from evidence that does exist - see ADR3-M8-002:

  * `secret_exposure_incidents` is the failure count of the secret-scan suites,
    which walk every surface a credential could reach.
  * `capability_policy_bypass` is derived from `CapabilityGateway` audit rows by
    `capability_policy_bypasses()` below, and additionally exercised by the
    governance suites. A bypass is an audited execution that ran despite its own
    authorization saying it must not.

Usage:

    uv run python scripts/release_gate.py            # full gate
    uv run python scripts/release_gate.py --json     # machine-readable

Exit code is 0 only when every condition passes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from accretion.contracts import (  # noqa: E402
    AuthorizationOutcome,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
)

# Executions that actually reached the backend. DENIED and REQUIRES_APPROVAL
# never ran; UNKNOWN is the gateway's "did not observe a terminal outcome" and
# is treated as executed, because a call that may have run and cannot be shown
# not to have is not evidence of no bypass.
EXECUTED_STATUSES = frozenset(
    {
        CapabilityExecutionStatus.EXECUTING,
        CapabilityExecutionStatus.SUCCEEDED,
        CapabilityExecutionStatus.FAILED,
        CapabilityExecutionStatus.UNKNOWN,
    }
)


def capability_policy_bypasses(
    results: Iterable[CapabilityExecutionResult],
) -> list[str]:
    """Return one description per audited execution that bypassed its policy.

    A bypass is a call whose authorization refused it - DENY, or
    REQUIRE_APPROVAL with no approval recorded - and which nonetheless reached
    the backend. An audit row that records no policy at all is also a bypass:
    the gateway is required to decide before it executes, so an execution with
    no recorded decision is indistinguishable from one that skipped the check.
    """
    bypasses: list[str] = []
    for result in results:
        if result.status not in EXECUTED_STATUSES:
            continue
        capability = result.request.capability_id
        outcome = result.authorization.outcome
        if outcome is AuthorizationOutcome.DENY:
            bypasses.append(
                f"{capability}: executed with status {result.status.value} after DENY"
            )
        elif (
            outcome is AuthorizationOutcome.REQUIRE_APPROVAL
            and not result.authorization.approval_id
        ):
            bypasses.append(
                f"{capability}: executed with status {result.status.value} while "
                "approval was required and none was recorded"
            )
        if not result.authorization.policy_id:
            bypasses.append(f"{capability}: executed with no policy recorded")
    return bypasses


# Each condition names the suites that decide it. Naming files rather than
# running the whole suite is the point: a condition must be able to fail on its
# own evidence, not be carried by an unrelated green test elsewhere.
SECRET_SCAN_SUITES = [
    "tests/test_v03_m2_secret_scan.py",
    "tests/test_v03_m7_enterprise_secret_scan.py",
]
CONNECTION_ISOLATION_SUITES = [
    "tests/test_v03_m0_connections.py",
    "tests/test_v03_m2_token_broker.py",
    "tests/test_v03_m7_enterprise_auth.py",
    "tests/test_v03_m7_enterprise_mcp.py",
]
POLICY_BYPASS_SUITES = [
    "tests/test_p4_governance.py",
    "tests/test_v03_m8_release_gate.py",
]
REGRESSION_SUITES = [
    "tests/test_acr_arch.py",
    "tests/test_p5_dynamic_service.py",
    "tests/test_p6_search_contracts.py",
    "tests/test_p7_experience_service.py",
    "tests/test_v03_m8_inherited_planning.py",
    "tests/test_v03_m8_inherited_search.py",
    "tests/test_v03_m8_benchmark_versioning.py",
    "tests/test_v03_m8_experience_evidence.py",
]


@dataclass
class Condition:
    name: str
    expression: str
    passed: bool
    detail: str
    missing: list[str] = field(default_factory=list)


def existing(paths: list[str]) -> tuple[list[str], list[str]]:
    present = [item for item in paths if (ROOT / item).exists()]
    missing = [item for item in paths if not (ROOT / item).exists()]
    return present, missing


def run_pytest(paths: list[str]) -> tuple[bool, str]:
    """Run a named set of suites in a subprocess and summarize the outcome.

    An empty path list is refused rather than passed to pytest. Bare `pytest`
    collects the whole repository - including the suite that tests this script,
    which would then invoke it again - and, worse, a condition backed by no
    suites would report the whole suite's verdict instead of its own. Nothing
    named means no evidence, which is a failure.
    """
    if not paths:
        return False, "no suites named for this condition"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_asyncio.plugin",
            "-q",
            "--no-header",
            *paths,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**_pytest_env()},
    )
    tail = [line for line in completed.stdout.strip().splitlines() if line.strip()]
    summary = tail[-1] if tail else "no pytest output"
    return completed.returncode == 0, summary


def _pytest_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    # Match the Makefile: the autoload guard and the explicit asyncio plugin are
    # required together; the env var alone breaks every async test.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def acceptance_condition() -> Condition:
    completed = subprocess.run(
        [sys.executable, "scripts/check_acceptance.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_pytest_env(),
    )
    counts = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith("in scope:")
    ]
    detail = counts[-1] if counts else "check_acceptance.py produced no counts line"
    return Condition(
        name="acceptance",
        expression="all(MUST acceptance criteria pass)",
        passed=completed.returncode == 0,
        detail=detail,
    )


def suite_condition(name: str, expression: str, paths: list[str]) -> Condition:
    present, missing = existing(paths)
    if missing:
        return Condition(
            name=name,
            expression=expression,
            passed=False,
            detail=f"named suite is missing: {', '.join(missing)}",
            missing=missing,
        )
    passed, summary = run_pytest(present)
    return Condition(
        name=name,
        expression=expression,
        passed=passed,
        detail=f"{len(present)} suite(s): {summary}",
    )


def evaluate() -> list[Condition]:
    return [
        acceptance_condition(),
        suite_condition(
            "secret_exposure_incidents",
            "secret_exposure_incidents == 0",
            SECRET_SCAN_SUITES,
        ),
        suite_condition(
            "capability_policy_bypass",
            "capability_policy_bypass == 0",
            POLICY_BYPASS_SUITES,
        ),
        suite_condition(
            "connection_isolation_tests",
            "connection_isolation_tests == PASS",
            CONNECTION_ISOLATION_SUITES,
        ),
        suite_condition(
            "regression_suite",
            "v0.1/v0.2 regression suite == PASS",
            REGRESSION_SUITES,
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the SDD 24.8 release gate.")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    conditions = evaluate()
    passed = all(item.passed for item in conditions)

    if args.json:
        payload = {
            "passed": passed,
            "conditions": [asdict(item) for item in conditions],
        }
        print(json.dumps(payload, indent=2))
    else:
        print("=== Release gate (SDD 24.8) ===\n")
        width = max(len(item.expression) for item in conditions)
        for item in conditions:
            verdict = "PASS" if item.passed else "FAIL"
            print(f"  {verdict}  {item.expression.ljust(width)}   {item.detail}")
        print()
        print(
            "PASS: every release-gate condition holds."
            if passed
            else "FAIL: the release gate is not satisfied."
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
