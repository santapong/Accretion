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
from datetime import date, timedelta
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

# Frontend evidence is one or more ``path[:line] <test title>`` segments joined by
# ``" + "``. The pointer and the title it carries are both machine-checked.
_EVIDENCE_POINTER = re.compile(r"[A-Za-z0-9_./-]+\.(?:tsx|ts|jsx|js)(?::\d+)?")
FRONTEND_EVIDENCE_ROOT = "apps/ui/"
FRONTEND_EVIDENCE_SUFFIXES = (".test.ts", ".test.tsx")
_EVIDENCE_SEPARATOR = " + "


def _vitest_anchor(title: str) -> re.Pattern[str]:
    """Match the opening line of a vitest ``test``/``it`` declaring exactly ``title``."""

    return re.compile(r"^\s*(?:test|it)\(\s*(['\"`])" + re.escape(title) + r"\1")
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
    "EMA": "M7",
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
    frontend_evidence: str = ""
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


POLICY_KEYS = frozenset(
    {
        "verification",
        "reason",
        "issue",
        "expires",
        "evidence",
        "last_verified",
        "frontend_evidence",
    }
)
VERIFICATION_MODES = frozenset({"test", "not_yet_due", "waived", "manual", "frontend"})


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
        # A key nothing validates is a silent hole: a one-character typo in
        # `frontend_evidence` would drop every checked vitest pointer while the gate
        # still printed PASS. Unknown keys and unknown verification modes fail closed.
        for key in entry:
            if key not in POLICY_KEYS:
                errors.append(f"{identifier}: unknown policy key {key!r}")
        verification = entry.get("verification", "test")
        if verification not in VERIFICATION_MODES:
            errors.append(
                f"{identifier}: unknown verification {verification!r} "
                f"(expected one of {', '.join(sorted(VERIFICATION_MODES))})"
            )
        criterion.verification = verification
        criterion.reason = entry.get("reason", "")
        criterion.issue = entry.get("issue", "")
        criterion.expires = entry.get("expires", "")
        criterion.evidence = entry.get("evidence", "")
        criterion.last_verified = entry.get("last_verified", "")
        criterion.frontend_evidence = entry.get("frontend_evidence", "")
        if criterion.frontend_evidence:
            # A criterion whose pytest claim covers only part of the surface can name
            # the vitest spec carrying the rest. The pointer is checked exactly like
            # `verification = "frontend"` evidence, so deleting the page test fails the
            # gate even though the criterion is still counted as proven by pytest.
            errors.extend(
                frontend_evidence_errors(identifier, criterion.frontend_evidence)
            )
        if criterion.verification == "waived":
            # A waiver without an owner or an end date becomes permanent silence.
            if not criterion.reason or not criterion.issue or not criterion.expires:
                errors.append(f"{identifier}: waiver needs reason, issue, and expires")
            elif criterion.expires:
                errors.extend(waiver_expiry_errors(identifier, criterion.expires))
        if criterion.verification == "manual":
            if not criterion.evidence or not criterion.last_verified:
                errors.append(f"{identifier}: manual needs evidence and last_verified")
        if criterion.verification == "frontend":
            errors.extend(frontend_evidence_errors(identifier, criterion.evidence))
    return errors


MAX_WAIVER_DAYS = 180


def waiver_expiry_errors(identifier: str, value: str) -> list[str]:
    """Check that a waiver ends on a real, near-enough date.

    ``expires`` is the only thing that makes a waiver temporary, and it is checked
    here rather than at classification time so that an unusable value is a policy
    error a human must fix instead of a silent grant. A release name --- ``expires =
    "v0.4.0"`` --- is not a date any calendar can pass, and a date decades out is a
    waiver in name only, so both are refused. The horizon matches the staleness
    window for manual evidence: whatever was believed today must be re-argued within
    half a year.
    """

    try:
        expires = date.fromisoformat(value)
    except ValueError:
        return [
            f"{identifier}: waiver expires {value!r} is not an ISO-8601 date "
            f"(YYYY-MM-DD); a release name never comes due"
        ]
    horizon = date.today() + timedelta(days=MAX_WAIVER_DAYS)
    if expires > horizon:
        return [
            f"{identifier}: waiver expires {value} is more than {MAX_WAIVER_DAYS} "
            f"days out (at most {horizon.isoformat()})"
        ]
    return []


def frontend_evidence_errors(identifier: str, evidence: str) -> list[str]:
    """Check that frontend evidence anchors on a vitest test that actually exists.

    ``verification = "frontend"`` moves the proof out of this gate and into vitest, so
    the pointer is the only thing left tying a criterion to a test. An unchecked string
    rots silently the moment a test file is renamed or deleted; a bare file path is
    barely better, because any ``.ts`` under ``apps/ui/`` — a page source, the vitest
    setup file — would satisfy it while proving nothing. Each pointer must therefore be
    an ``apps/ui/...`` path naming a vitest spec (``*.test.ts`` / ``*.test.tsx``), must
    carry a ``:line`` anchor onto the test it claims, and the anchored line must open a
    ``test``/``it`` whose title is exactly the prose the pointer carries. A file, a line
    or a title that drifts fails here, in the gate, not only in the pytest suite.
    """

    errors: list[str] = []
    segments: list[tuple[str, str]] = []
    for segment in evidence.split(_EVIDENCE_SEPARATOR):
        match = _EVIDENCE_POINTER.search(segment)
        if match is None:
            continue
        segments.append((match.group(0), segment[match.end() :].strip()))
    if not segments:
        errors.append(
            f"{identifier}: frontend needs evidence naming the vitest test "
            f"as a '{FRONTEND_EVIDENCE_ROOT}...' path"
        )
        return errors
    for pointer, title in segments:
        relative, _, line = pointer.partition(":")
        if not relative.startswith(FRONTEND_EVIDENCE_ROOT):
            errors.append(
                f"{identifier}: frontend evidence '{pointer}' must be a path "
                f"under {FRONTEND_EVIDENCE_ROOT}"
            )
            continue
        if not relative.endswith(FRONTEND_EVIDENCE_SUFFIXES):
            errors.append(
                f"{identifier}: frontend evidence '{relative}' must name a vitest spec "
                f"({' or '.join(FRONTEND_EVIDENCE_SUFFIXES)})"
            )
            continue
        target = ROOT / relative
        if not target.is_file():
            errors.append(f"{identifier}: frontend evidence path does not exist: {relative}")
            continue
        if not line:
            errors.append(
                f"{identifier}: frontend evidence {relative} needs a :line anchor "
                f"naming the vitest test"
            )
            continue
        source = target.read_text().splitlines()
        total = len(source)
        if not 1 <= int(line) <= total:
            errors.append(
                f"{identifier}: frontend evidence {relative}:{line} is past "
                f"end of file ({total} lines)"
            )
            continue
        if not title:
            errors.append(
                f"{identifier}: frontend evidence {relative}:{line} names no test title"
            )
            continue
        anchor = source[int(line) - 1]
        if not _vitest_anchor(title).match(anchor):
            errors.append(
                f"{identifier}: frontend evidence {relative}:{line} lands on "
                f"{anchor!r}, not the test it names"
            )
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
        """Record the outcome, including the phases that never reach the test body.

        A fixture that raises produces a ``setup`` report and no ``call`` report at
        all, so a claim whose setup blew up would otherwise leave no outcome behind
        and be indistinguishable from a test that was never collected. A teardown
        failure is equally load-bearing: it can mean the assertion passed only
        because the resource it was asserting on was never really there. Both are
        recorded as ``error`` and classify as FAILING.
        """

        if report.when == "call":
            self.outcomes[report.nodeid] = report.outcome
        elif report.when == "setup":
            if report.outcome == "skipped":
                self.outcomes[report.nodeid] = "skipped"
            elif report.outcome == "failed":
                self.outcomes[report.nodeid] = "error"
        elif report.when == "teardown" and report.outcome == "failed":
            self.outcomes[report.nodeid] = "error"


def run_tests(quiet: bool) -> AcceptancePlugin:
    import pytest

    plugin = AcceptancePlugin()
    args = ["-p", "pytest_asyncio.plugin", "--no-header"]
    args.append("-q" if quiet else "-v")
    code = pytest.main(args, plugins=[plugin])
    if code not in {0, 1}:  # 1 = tests failed, which we report per criterion
        raise SystemExit(f"pytest exited with {code}")
    return plugin


# An outcome that is not a clean pass. ``missing`` is what the CLI records for a
# claimed node that reported nothing at all --- deselected, collected but never run,
# or crashed before pytest could file a report --- and ``error`` is a setup or
# teardown failure. Neither is evidence of anything.
FAILED_OUTCOMES = frozenset({"failed", "error", "missing"})


def classify(criterion: Criterion) -> str:
    if criterion.verification == "not_yet_due":
        return "NOT_YET_DUE"
    if any(outcome in FAILED_OUTCOMES for outcome in criterion.outcomes):
        # Checked before the verification mode, not after: a manual record or a
        # waiver describes what a human believed, and it must not silence a test
        # that claims the same criterion and is failing right now. The failing test
        # is the newer, more specific evidence.
        return "FAILING"
    if criterion.verification == "waived":
        if criterion.expires and _expired(criterion.expires):
            return "WAIVER_EXPIRED"
        return "WAIVED"
    if criterion.verification == "manual":
        if criterion.last_verified and _stale(criterion.last_verified):
            return "MANUAL_STALE"
        return "MANUAL"
    if criterion.verification == "frontend":
        # Proven by the vitest suite, which CI runs via `npm run test`. This gate reads
        # pytest markers only, so it records the pointer rather than re-proving it.
        return "FRONTEND"
    if not criterion.tests:
        return "UNCOVERED"
    if all(outcome == "skipped" for outcome in criterion.outcomes):
        # A skipped test proves nothing.
        return "SKIPPED_ONLY"
    return "PROVEN"


def _expired(value: str) -> bool:
    try:
        return date.fromisoformat(value) < date.today()
    except ValueError:
        # Not a date any calendar can compare --- a release name, a typo, an empty
        # string. Failing open here would turn `expires = "v0.4.0"` into a permanent
        # waiver, so an unreadable end date counts as already past. `apply_policy`
        # refuses the same value as a policy error; this is the second door.
        return True


def _stale(value: str, max_age_days: int = 180) -> bool:
    try:
        return (date.today() - date.fromisoformat(value)).days > max_age_days
    except ValueError:
        return True


_FAILING = {"UNCOVERED", "FAILING", "SKIPPED_ONLY", "WAIVER_EXPIRED", "MANUAL_STALE"}


