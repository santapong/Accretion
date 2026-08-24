"""Acceptance criteria gate (SDD sections 20 / 19 / 24).

The three SDDs are the source of truth for *what* the criteria are; this script
parses them directly rather than working from a generated copy that could drift.
``docs/acceptance/criteria.toml`` records only *how* each criterion is verified, and
anything absent from it must be proven by a test that claims it:

    @pytest.mark.acceptance("AC3-CON-03")

Exit status is non-zero when any in-scope MUST criterion is unproven, so this can gate
a release. Run with ``make acceptance``.
"""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
POLICY_PATH = ROOT / "docs" / "acceptance" / "criteria.toml"

SDDS = {
    "v0.1": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.1.md",
    "v0.2": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.2.md",
    "v0.3": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.3.md",
}

_ROW = re.compile(r"^\|\s*((?:V0[12]|AC3)[A-Z0-9-]+)\s*\|(.+)\|\s*$")
_PRIORITIES = {"MUST", "SHOULD"}

# v0.3 criteria carry their milestone in the category, not the id.
_CATEGORY_MILESTONE = {
    "ID": "M1",
    "CON": "M2",
    "SEC": "M2",
    "MCP": "M3",
    "PLG": "M4",
    "RES": "M5",
    "UI": "M6",
}


@dataclass
class Criterion:
    id: str
    release: str
    stage: str
    priority: str
    text: str
    source: str
    verification: str = "test"
    reason: str = ""
    issue: str = ""
    expires: str = ""
    evidence: str = ""
    last_verified: str = ""
    tests: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)

    @property
    def in_scope(self) -> bool:
        return self.verification != "not_yet_due"


def stage_of(criterion_id: str) -> str:
    """P-phase for v0.1/v0.2, milestone for v0.3.

    Not every inherited id encodes a phase: v0.1 files its benchmark gate under
    ``V01-BENCH-*`` and v0.2 its frontend criteria under ``V02-UI-*``.
    """

    phase = re.match(r"^V0[12]-(P[0-9])-", criterion_id)
    if phase:
        return phase.group(1)
    grouped = re.match(r"^V0([12])-([A-Z]+)-", criterion_id)
    if grouped:
        return f"v0.{grouped.group(1)}-{grouped.group(2).lower()}"
    category = re.match(r"^AC3-([A-Z]+)-", criterion_id)
    if category:
        return _CATEGORY_MILESTONE.get(category.group(1), "unassigned")
    return "unassigned"


def load_criteria() -> dict[str, Criterion]:
    criteria: dict[str, Criterion] = {}
    for release, path in SDDS.items():
        if not path.exists():
            raise SystemExit(f"missing SDD: {path}")
        for number, line in enumerate(path.read_text().splitlines(), 1):
            match = _ROW.match(line)
            if not match:
                continue
            cells = [cell.strip() for cell in match.group(2).split("|") if cell.strip()]
            priority = next((cell for cell in cells if cell in _PRIORITIES), "")
            if not priority:
                continue
            text = next((cell for cell in cells if cell not in _PRIORITIES), "")
            identifier = match.group(1)
            criteria[identifier] = Criterion(
                id=identifier,
                release=release,
                stage=stage_of(identifier),
                priority=priority,
                text=text,
                source=f"{path.relative_to(ROOT)}:{number}",
            )
    return criteria


def apply_policy(criteria: dict[str, Criterion]) -> list[str]:
    """Overlay the verification policy. Returns policy errors."""

    errors: list[str] = []
    if not POLICY_PATH.exists():
        return [f"missing policy file: {POLICY_PATH.relative_to(ROOT)}"]
    policy = tomllib.loads(POLICY_PATH.read_text())
    for identifier, entry in policy.get("criteria", {}).items():
        criterion = criteria.get(identifier)
        if criterion is None:
            errors.append(f"{identifier}: named in policy but absent from every SDD")
            continue
        criterion.verification = entry.get("verification", "test")
        criterion.reason = entry.get("reason", "")
        criterion.issue = entry.get("issue", "")
        criterion.expires = entry.get("expires", "")
        criterion.evidence = entry.get("evidence", "")
        criterion.last_verified = entry.get("last_verified", "")
        if criterion.verification == "waived":
            # A waiver without an owner or an end date becomes permanent silence.
            if not criterion.reason or not criterion.issue or not criterion.expires:
                errors.append(f"{identifier}: waiver needs reason, issue, and expires")
        if criterion.verification == "manual":
            if not criterion.evidence or not criterion.last_verified:
                errors.append(f"{identifier}: manual needs evidence and last_verified")
    return errors


class AcceptancePlugin:
    """Collects which tests claim which criteria, and how those tests fared."""

    def __init__(self) -> None:
        self.claims: dict[str, list[str]] = defaultdict(list)
        self.outcomes: dict[str, str] = {}

    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        for item in items:
            for marker in item.iter_markers("acceptance"):
                for identifier in marker.args:
                    self.claims[identifier].append(item.nodeid)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "call":
            self.outcomes[report.nodeid] = report.outcome
        elif report.when == "setup" and report.outcome == "skipped":
            self.outcomes[report.nodeid] = "skipped"


def run_tests(quiet: bool) -> AcceptancePlugin:
    import pytest

    plugin = AcceptancePlugin()
    args = ["-p", "pytest_asyncio.plugin", "--no-header"]
    args.append("-q" if quiet else "-v")
    code = pytest.main(args, plugins=[plugin])
    if code not in {0, 1}:  # 1 = tests failed, which we report per criterion
        raise SystemExit(f"pytest exited with {code}")
    return plugin


def classify(criterion: Criterion) -> str:
    if criterion.verification == "not_yet_due":
        return "NOT_YET_DUE"
    if criterion.verification == "waived":
        if criterion.expires and _expired(criterion.expires):
            return "WAIVER_EXPIRED"
        return "WAIVED"
    if criterion.verification == "manual":
        if criterion.last_verified and _stale(criterion.last_verified):
            return "MANUAL_STALE"
        return "MANUAL"
    if not criterion.tests:
        return "UNCOVERED"
    if any(outcome == "failed" for outcome in criterion.outcomes):
        return "FAILING"
    if all(outcome == "skipped" for outcome in criterion.outcomes):
        # A skipped test proves nothing.
        return "SKIPPED_ONLY"
    return "PROVEN"


def _expired(value: str) -> bool:
    try:
        return date.fromisoformat(value) < date.today()
    except ValueError:
        # A release name rather than a date; a human must retire it.
        return False


def _stale(value: str, max_age_days: int = 180) -> bool:
    try:
        return (date.today() - date.fromisoformat(value)).days > max_age_days
    except ValueError:
        return True


_FAILING = {"UNCOVERED", "FAILING", "SKIPPED_ONLY", "WAIVER_EXPIRED", "MANUAL_STALE"}


