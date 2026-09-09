"""Acceptance criteria registration and evidence gates for the active SDDs.

The active SDDs are the source of truth for *what* the criteria are; this module
parses them directly rather than working from a generated copy that could drift.
``docs/acceptance/criteria.toml`` records only *how* each criterion is verified, and
anything absent from it must be proven by a test that claims it:

    @pytest.mark.acceptance("AC3-CON-03")

Exit status is non-zero when any in-scope MUST criterion is unproven, so this can gate
a release. Run with ``make acceptance``.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
POLICY_PATH = ROOT / "docs" / "acceptance" / "criteria.toml"

SDDS = {
    "v0.1": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.1.md",
    "v0.2": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.2.md",
    "v0.3": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.3.md",
    "v0.4": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.4.md",
    "v0.5": ROOT / "docs" / "sdd" / "Accretion_SDD_v0.5.md",
}

_ROW = re.compile(r"^\|\s*((?:V0[12]|AC3|AC4)[A-Z0-9-]+)\s*\|(.+)\|\s*$")
_V05_ROW = re.compile(
    r"^\|\s*(AC5-[0-9]{3})\s*\|\s*(MUST)\s*\|\s*(M[0-9])\s*\|"
    r"\s*([ASUB](?:\+[ASUB])*)\s*\|\s*([^|]+?)\s*\|\s*$"
)
V05_IDS = frozenset(f"AC5-{number:03d}" for number in range(1, 31))
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_ID = re.compile(r"^[0-9a-f]{40}$")

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
    required_evidence: tuple[str, ...] = ()
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
    """P-phase for v0.1/v0.2, milestone for v0.3, release-prefixed milestone for v0.4.

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
    # v0.4 ids carry their owning milestone (SDD v0.4 ADR-052). The stage is
    # prefixed with the release so that ``--stage M4`` keeps meaning the v0.3
    # plugin manager and can never select v0.4's offline ranker by accident.
    owned = re.match(r"^AC4-(M[0-9]+)-[0-9]{3}$", criterion_id)
    if owned:
        return f"v0.4-{owned.group(1)}"
    return "unassigned"


def load_criteria() -> dict[str, Criterion]:
    criteria: dict[str, Criterion] = {}
    for release, path in SDDS.items():
        if not path.exists():
            raise SystemExit(f"missing SDD: {path}")
        release_ids: set[str] = set()
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if release == "v0.5":
                match_v05 = _V05_ROW.match(line)
                if match_v05 is None:
                    if re.match(r"^\s*\|\s*(?:AC|V0)[A-Z0-9-]", line):
                        raise SystemExit(f"{path}:{number}: malformed v0.5 criterion row")
                    continue
                identifier, priority, owner, witnesses, text = match_v05.groups()
                if identifier not in V05_IDS:
                    raise SystemExit(f"{path}:{number}: unknown v0.5 criterion {identifier}")
                if len(set(witnesses.split("+"))) != len(witnesses.split("+")):
                    raise SystemExit(f"{path}:{number}: duplicate witness class")
                if identifier in criteria:
                    raise SystemExit(f"{path}:{number}: duplicate criterion {identifier}")
                criteria[identifier] = Criterion(
                    id=identifier,
                    release=release,
                    stage=f"v0.5-{owner}",
                    priority=priority,
                    text=text,
                    source=f"{path.relative_to(ROOT)}:{number}",
                    required_evidence=tuple(witnesses.split("+")),
                )
                release_ids.add(identifier)
                continue
            match = _ROW.match(line)
            if not match:
                continue
            cells = [cell.strip() for cell in match.group(2).split("|") if cell.strip()]
            priority = next((cell for cell in cells if cell in _PRIORITIES), "")
            if not priority:
                continue
            text = next((cell for cell in cells if cell not in _PRIORITIES), "")
            identifier = match.group(1)
            if identifier in criteria:
                raise SystemExit(f"{path}:{number}: duplicate criterion {identifier}")
            criteria[identifier] = Criterion(
                id=identifier,
                release=release,
                stage=stage_of(identifier),
                priority=priority,
                text=text,
                source=f"{path.relative_to(ROOT)}:{number}",
            )
            release_ids.add(identifier)
        if not release_ids:
            raise SystemExit(f"{path}: no acceptance criteria loaded for {release}")
        if release == "v0.5" and release_ids != V05_IDS:
            raise SystemExit(
                f"{path}: missing v0.5 criteria: {', '.join(sorted(V05_IDS - release_ids))}"
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
        if criterion.release == "v0.5" and (
            not isinstance(entry, dict)
            or any(
                key in POLICY_KEYS and not isinstance(value, str) for key, value in entry.items()
            )
        ):
            errors.append(f"{identifier}: v0.5 policy must be a table of string values")
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
            errors.extend(frontend_evidence_errors(identifier, criterion.frontend_evidence))
        if criterion.verification == "waived":
            # A waiver without an owner or an end date becomes permanent silence.
            if not criterion.reason or not criterion.issue or not criterion.expires:
                errors.append(f"{identifier}: waiver needs reason, issue, and expires")
            elif criterion.expires:
                errors.extend(waiver_expiry_errors(identifier, criterion.expires))
        if criterion.verification == "manual":
            if not criterion.evidence or not criterion.last_verified:
                errors.append(f"{identifier}: manual needs evidence and last_verified")
            elif criterion.release == "v0.5":
                if not _local_file(ROOT, criterion.evidence):
                    errors.append(f"{identifier}: manual evidence must name an existing local file")
                try:
                    if date.fromisoformat(criterion.last_verified) > date.today():
                        errors.append(f"{identifier}: manual verification date is in the future")
                except ValueError:
                    errors.append(f"{identifier}: invalid manual verification date")
        if criterion.release == "v0.5" and criterion.verification == "not_yet_due":
            if (
                not criterion.reason
                or not criterion.issue
                or not _local_file(ROOT, criterion.issue)
            ):
                errors.append(f"{identifier}: deferral needs reason and an existing program record")
        if criterion.verification == "frontend":
            errors.extend(frontend_evidence_errors(identifier, criterion.evidence))
    return errors


def _local_file(root: Path, relative: str) -> Path | None:
    """Evidence paths cannot escape their bundle, including through symlinks."""
    parts = Path(relative).parts
    if not relative or Path(relative).is_absolute() or ".." in parts:
        return None
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        return None
    return resolved


def _json_object(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("evidence metadata exceeds 8 MiB")
    value = json.loads(path.read_text(), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("evidence metadata must be an object")
    return value


def _artifact(root: Path, pointer: Any) -> Path:
    if not isinstance(pointer, dict) or not isinstance(pointer.get("path"), str):
        raise ValueError("artifact requires path and SHA-256")
    expected = pointer.get("sha256")
    if not isinstance(expected, str) or not _DIGEST.fullmatch(expected):
        raise ValueError("artifact requires a valid SHA-256")
    path = _local_file(root, pointer["path"])
    if path is None or path.stat().st_size == 0:
        raise ValueError("artifact is absent, empty or outside its bundle")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"artifact digest mismatch: {pointer['path']}")
    return path


def _recorded_time(value: Any, now: datetime) -> datetime:
    if not isinstance(value, str):
        raise ValueError("execution timestamp is required")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("execution timestamp needs a timezone")
    if stamp > now or now - stamp > timedelta(days=180):
        raise ValueError("execution timestamp is future-dated or stale")
    return stamp


def _digests(value: Any, minimum: int, label: str) -> None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not _DIGEST.fullmatch(item) for item in value)
        or len(set(value)) != len(value)
        or len(value) < minimum
    ):
        raise ValueError(f"{label} requires at least {minimum} distinct SHA-256 identities")


def _witness_errors(
    root: Path,
    pointer: Any,
    criterion: Criterion,
    kind: str,
    candidate_commit: str,
    candidate_tree: str,
    now: datetime,
) -> list[str]:
    """Validate attributable records, not the truth of an unexecuted experiment.

    Actual witness producers and independent qualification are required by the
    SDD. A synthetic report used to test this parser is never release evidence.
    """
    try:
        report = _json_object(_artifact(root, pointer))
        expected = {
            "schema_version": 1,
            "criterion_id": criterion.id,
            "witness_class": kind,
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "outcome": "PASS",
        }
        for key, value in expected.items():
            if type(report.get(key)) is not type(value) or report.get(key) != value:
                raise ValueError(f"report {key} does not match {value!r}")
        executed_at = _recorded_time(report.get("executed_at"), now)
        if type(report.get("exit_code")) is not int or report["exit_code"] != 0:
            raise ValueError("report needs an observed zero command exit")
        command = report.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part.strip() for part in command)
        ):
            raise ValueError("report needs the executed command")
        cases = report.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("report has no executed cases")
        case_ids: set[str] = set()
        for case in cases:
            if (
                not isinstance(case, dict)
                or not isinstance(case.get("id"), str)
                or not case["id"].strip()
                or case["id"] in case_ids
                or case.get("outcome") != "PASS"
            ):
                raise ValueError("report cases must have unique IDs and actual PASS outcomes")
            case_ids.add(case["id"])
        artifacts = report.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("report needs original execution artifacts")
        for artifact in artifacts:
            _artifact(root, artifact)
        if kind in {"S", "B"}:
            if report.get("evidence_type") != "SIMULATION":
                raise ValueError("simulator/study report must retain SIMULATION evidence type")
            producer, verifier = report.get("producer_identity"), report.get("verifier_identity")
            if (
                not isinstance(producer, str)
                or not isinstance(verifier, str)
                or (not producer.strip() or not verifier.strip() or producer == verifier)
            ):
                raise ValueError("independent producer and verifier identities are required")
            _digests(report.get("simulator_digests"), 1, "simulator_digests")
            minimum = 2 if kind == "B" or criterion.id in {"AC5-006", "AC5-007"} else 1
            _digests(report.get("adapter_digests"), minimum, "adapter_digests")
        if kind == "B":
            _artifact(root, report.get("protocol"))
            registered = _recorded_time(report.get("registered_at"), now)
            first_outcome = _recorded_time(report.get("first_outcome_at"), now)
            if not registered < first_outcome <= executed_at:
                raise ValueError("protocol must be registered before study outcome access")
            if report.get("claim_decision") != "GO":
                raise ValueError("NO-GO or INCONCLUSIVE does not qualify the release claim")
            count = report.get("planned_trials")
            if (
                type(count) is not int
                or count <= 0
                or type(report.get("completed_trials")) is not int
                or report.get("completed_trials") != count
            ):
                raise ValueError("registered trial matrix is empty or incomplete")
            for key, values in {
                "treatments": {"B0", "B1", "B2", "B3"},
                "task_families": {"reach", "pick_place", "declared_recovery"},
            }.items():
                actual = report.get(key)
                if (
                    not isinstance(actual, list)
                    or len(actual) != len(values)
                    or (any(not isinstance(item, str) for item in actual) or set(actual) != values)
                ):
                    raise ValueError(f"study does not cover every required {key}")
    except (OSError, ValueError, TypeError) as error:
        return [f"{criterion.id}/{kind}: {error}"]
    return []


def v05_release_evidence_errors(
    criteria: dict[str, Criterion],
    manifest_path: Path | None,
    candidate_commit: str,
    candidate_tree: str,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Require all v0.5 composite records in addition to ordinary acceptance.

    The bundle normally lives outside the clean candidate checkout, avoiding a
    self-referential commit hash. It is archived with the later release audit.
    No deferral or waiver can hide an original v0.5 release obligation.
    """
    errors: list[str] = []
    selected = {key: item for key, item in criteria.items() if item.release == "v0.5"}
    if set(selected) != V05_IDS:
        errors.append("v0.5 release needs exactly all 30 registered criteria")
    for criterion in selected.values():
        if criterion.verification in {"not_yet_due", "waived"}:
            errors.append(f"{criterion.id}: {criterion.verification} cannot qualify a v0.5 release")
        if not criterion.required_evidence:
            errors.append(f"{criterion.id}: no required witness classes registered")
    if not _GIT_ID.fullmatch(candidate_commit) or not _GIT_ID.fullmatch(candidate_tree):
        errors.append("release evidence requires full candidate commit and tree identities")
    if manifest_path is None:
        return [*errors, "v0.5 release requires a composite evidence manifest"]
    try:
        manifest = _json_object(manifest_path)
        for key, value in {
            "schema_version": 1,
            "release": "v0.5",
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
        }.items():
            if type(manifest.get(key)) is not type(value) or manifest.get(key) != value:
                errors.append(f"release manifest {key} does not match candidate")
        entries = manifest.get("criteria")
        if not isinstance(entries, dict) or set(entries) != V05_IDS:
            return [*errors, "release manifest must cover exactly the 30 v0.5 IDs"]
        for identifier, criterion in selected.items():
            entry = entries[identifier]
            if not isinstance(entry, dict) or set(entry) != set(criterion.required_evidence):
                errors.append(f"{identifier}: missing or unknown composite witness classes")
                continue
            for kind in criterion.required_evidence:
                errors.extend(
                    _witness_errors(
                        manifest_path.parent,
                        entry[kind],
                        criterion,
                        kind,
                        candidate_commit,
                        candidate_tree,
                        now or datetime.now(UTC),
                    )
                )
    except (OSError, ValueError, TypeError) as error:
        errors.append(f"release manifest cannot be read: {error}")
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
            errors.append(f"{identifier}: frontend evidence {relative}:{line} names no test title")
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
    if any(outcome in FAILED_OUTCOMES for outcome in criterion.outcomes):
        # Checked before the verification mode, not after: a manual record or a
        # waiver describes what a human believed, and it must not silence a test
        # that claims the same criterion and is failing right now. The failing test
        # is the newer, more specific evidence.
        return "FAILING"
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
