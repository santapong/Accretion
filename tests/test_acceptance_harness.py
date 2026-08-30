"""The acceptance gate must be able to go red.

A harness that cannot fail is decoration, so every failure mode it claims to detect
is exercised here: a failing claim, an uncovered MUST, a skipped-only claim, an
expired waiver, and stale manual evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from accretion import acceptance as harness

REPO_ROOT = Path(__file__).resolve().parents[1]


def criterion(**overrides: object) -> object:
    defaults = {
        "id": "V01-P0-001",
        "release": "v0.1",
        "stage": "P0",
        "priority": "MUST",
        "text": "example",
        "source": "docs/sdd/example.md:1",
    }
    defaults.update(overrides)
    return harness.Criterion(**defaults)  # type: ignore[arg-type]


def test_a_criterion_with_no_claiming_test_is_uncovered() -> None:
    assert harness.classify(criterion()) == "UNCOVERED"


def test_a_failing_claim_is_reported_failing_not_proven() -> None:
    entry = criterion(tests=["t::a", "t::b"], outcomes=["passed", "failed"])
    assert harness.classify(entry) == "FAILING"


def test_a_claim_that_only_ever_skips_proves_nothing() -> None:
    """Live-provider tests skip in CI; a skipped test must not count as evidence."""

    entry = criterion(tests=["t::live"], outcomes=["skipped"])
    assert harness.classify(entry) == "SKIPPED_ONLY"


def test_a_passing_claim_is_proven() -> None:
    entry = criterion(tests=["t::a"], outcomes=["passed"])
    assert harness.classify(entry) == "PROVEN"


def test_an_expired_waiver_fails_the_gate() -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    entry = criterion(
        verification="waived", reason="deferred", issue="#52", expires=yesterday
    )
    assert harness.classify(entry) == "WAIVER_EXPIRED"


def test_a_live_waiver_is_accepted() -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    entry = criterion(
        verification="waived", reason="deferred", issue="#52", expires=tomorrow
    )
    assert harness.classify(entry) == "WAIVED"


def test_manual_evidence_goes_stale() -> None:
    old = (date.today() - timedelta(days=400)).isoformat()
    entry = criterion(verification="manual", evidence="docs/x.md", last_verified=old)
    assert harness.classify(entry) == "MANUAL_STALE"

    recent = (date.today() - timedelta(days=10)).isoformat()
    fresh = criterion(verification="manual", evidence="docs/x.md", last_verified=recent)
    assert harness.classify(fresh) == "MANUAL"


def test_a_frontend_criterion_is_reported_separately_from_a_pytest_claim() -> None:
    """This gate reads pytest markers; a vitest-proven criterion records where it lives."""

    entry = criterion(verification="frontend", evidence="apps/ui/src/App.test.tsx")
    assert harness.classify(entry) == "FRONTEND"
    # Distinct from PROVEN so a reader can tell which suite actually ran it.
    assert harness.classify(criterion(tests=["t::a"], outcomes=["passed"])) == "PROVEN"


def test_frontend_evidence_naming_a_missing_file_fails_policy() -> None:
    """A pointer into vitest is the whole proof, so it must not be allowed to rot."""

    errors = harness.frontend_evidence_errors(
        "V01-P4-004", "apps/ui/src/DeletedPage.test.tsx:12 renders the operator screens"
    )
    assert errors == [
        "V01-P4-004: frontend evidence path does not exist: apps/ui/src/DeletedPage.test.tsx"
    ]


def test_frontend_evidence_pointing_past_the_end_of_a_file_fails_policy() -> None:
    length = len((harness.ROOT / "apps/ui/src/App.test.tsx").read_text().splitlines())
    errors = harness.frontend_evidence_errors(
        "V01-P4-004", f"apps/ui/src/App.test.tsx:{length + 1} renders the routes"
    )
    assert errors == [
        f"V01-P4-004: frontend evidence apps/ui/src/App.test.tsx:{length + 1} "
        f"is past end of file ({length} lines)"
    ]


def test_frontend_evidence_outside_the_ui_tree_fails_policy() -> None:
    errors = harness.frontend_evidence_errors("V01-P4-004", "src/accretion/acceptance.py:1 x")
    assert errors == [
        "V01-P4-004: frontend needs evidence naming the vitest test as a 'apps/ui/...' path"
    ]
    errors = harness.frontend_evidence_errors("V01-P4-004", "tests/test_acceptance_harness.ts x")
    assert errors == [
        "V01-P4-004: frontend evidence 'tests/test_acceptance_harness.ts' "
        "must be a path under apps/ui/"
    ]


def test_frontend_evidence_that_resolves_passes_policy() -> None:
    evidence = (
        "apps/ui/src/App.test.tsx:132 navigates to the required operator screens"
        " + apps/ui/src/EventStream.test.tsx:61 recovers a missed SSE sequence "
        "from the authoritative audit snapshot"
    )
    assert harness.frontend_evidence_errors("V01-P4-004", evidence) == []


def test_frontend_evidence_without_a_line_anchor_fails_policy() -> None:
    """Existence alone proves nothing: the pointer must name one test, not one file."""

    errors = harness.frontend_evidence_errors(
        "V01-P4-004", "apps/ui/src/App.test.tsx navigates to the required operator screens"
    )
    assert errors == [
        "V01-P4-004: frontend evidence apps/ui/src/App.test.tsx needs a :line anchor "
        "naming the vitest test"
    ]
    # Differential: the identical pointer with an anchor is accepted.
    assert (
        harness.frontend_evidence_errors(
            "V01-P4-004",
            "apps/ui/src/App.test.tsx:132 navigates to the required operator screens",
        )
        == []
    )


def test_frontend_evidence_whose_line_drifts_off_its_test_fails_policy() -> None:
    """One inserted line above the anchor must redden the gate, not just the pytest suite.

    The line number and the title are one claim: *this* test proves the criterion. A
    pointer that still resolves to a real spec file but lands on an import, a helper or
    a neighbouring test proves nothing, so the gate reads the anchored line itself.
    """

    real = "apps/ui/src/App.test.tsx:132 navigates to the required operator screens"
    assert harness.frontend_evidence_errors("V01-P4-004", real) == []

    drifted = "apps/ui/src/App.test.tsx:133 navigates to the required operator screens"
    source = (harness.ROOT / "apps/ui/src/App.test.tsx").read_text().splitlines()
    assert harness.frontend_evidence_errors("V01-P4-004", drifted) == [
        f"V01-P4-004: frontend evidence apps/ui/src/App.test.tsx:133 lands on "
        f"{source[132]!r}, not the test it names"
    ]

    renamed = "apps/ui/src/App.test.tsx:132 navigates to some other screens"
    assert harness.frontend_evidence_errors("V01-P4-004", renamed) == [
        f"V01-P4-004: frontend evidence apps/ui/src/App.test.tsx:132 lands on "
        f"{source[131]!r}, not the test it names"
    ]

    untitled = "apps/ui/src/App.test.tsx:132"
    assert harness.frontend_evidence_errors("V01-P4-004", untitled) == [
        "V01-P4-004: frontend evidence apps/ui/src/App.test.tsx:132 names no test title"
    ]


def test_frontend_evidence_naming_a_non_test_source_file_fails_policy() -> None:
    """A page source or the vitest setup file is not a proof, however real its path."""

    for relative in ("apps/ui/src/api.ts", "apps/ui/src/test/setup.ts", "apps/ui/src/App.tsx"):
        assert harness.frontend_evidence_errors("V01-P4-004", f"{relative}:1 x") == [
            f"V01-P4-004: frontend evidence '{relative}' must name a vitest spec "
            f"(.test.ts or .test.tsx)"
        ], relative


def test_not_yet_due_is_out_of_scope_rather_than_failing() -> None:
    entry = criterion(verification="not_yet_due", reason="M4")
    assert harness.classify(entry) == "NOT_YET_DUE"
    assert entry.in_scope is False


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("V01-P0-001", "P0"),
        ("V02-P7-008", "P7"),
        ("V01-BENCH-001", "v0.1-bench"),
        ("V02-UI-003", "v0.2-ui"),
        ("AC3-CON-06", "M2"),
        ("AC3-UI-01", "M6"),
        ("AC3-EMA-01", "M7"),
    ],
)
def test_every_id_shape_maps_to_a_stage(identifier: str, expected: str) -> None:
    assert harness.stage_of(identifier) == expected


def run_stage_gate(stage: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real CLI the way CI does, without running the pytest suite."""

    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_acceptance.py"),
            "--no-tests",
            "--stage",
            stage,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_a_stage_that_selects_no_criteria_is_an_error_rather_than_a_vacuous_pass() -> None:
    """A gate that matches nothing must go red: an empty selection is a typo."""

    result = run_stage_gate("M9")
    assert result.returncode != 0, result.stdout
    assert "PASS" not in result.stdout
    assert "selected no criteria" in result.stderr


def test_a_stage_that_selects_criteria_still_reports_them() -> None:
    """The empty-selection guard must not fire on a real stage."""

    result = run_stage_gate("M7")
    # ``--no-tests`` cannot see pytest claims, so a stage part-way through its build
    # reports its in-scope criteria as unmet. What must not happen is the *empty*
    # selection: exit code 2 with nothing reported.
    assert result.returncode != 2, result.stderr
    assert "selected no criteria" not in result.stderr
    # All seven §24.9 rows are now in scope, so the stage must report seven — an
    # empty selection would report none and still exit 0 without the guard.
    assert "in scope: 7" in result.stdout
    for index in range(1, 8):
        assert f"AC3-EMA-0{index}" in result.stdout


def test_the_sdds_still_parse_and_the_policy_is_well_formed() -> None:
    """Guards against an SDD edit that silently drops criteria from the gate."""

    criteria = harness.load_criteria()
    assert len(criteria) == 117, "expected 117 criteria across the three SDDs"
    assert sum(1 for c in criteria.values() if c.priority == "MUST") == 115
    assert harness.apply_policy(criteria) == []


# --- The frontend pointer must be load-bearing, end to end -------------------


def write_policy(tmp_path: object, evidence: str) -> object:
    """A single-criterion policy file standing in for docs/acceptance/criteria.toml."""

    path = tmp_path / "criteria.toml"  # type: ignore[operator]
    path.write_text(
        "[criteria]\n"
        f'V01-P4-004 = {{ verification = "frontend", evidence = "{evidence}" }}\n'
    )
    return path


def test_apply_policy_rejects_a_frontend_pointer_whose_test_file_was_deleted(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole gate, not just the helper, must go red when a pointer rots.

    Differential: the same criterion, the same policy shape, one pointer that resolves
    and one that does not. Only the second may produce an error, which rules out a
    helper that is written but never wired into ``apply_policy``.
    """

    criteria = {"V01-P4-004": criterion(id="V01-P4-004", stage="P4")}

    monkeypatch.setattr(
        harness, "POLICY_PATH", write_policy(
            tmp_path,
            "apps/ui/src/App.test.tsx:132 navigates to the required operator screens",
        )
    )
    assert harness.apply_policy(criteria) == []
    assert criteria["V01-P4-004"].verification == "frontend"

    monkeypatch.setattr(
        harness, "POLICY_PATH", write_policy(tmp_path, "apps/ui/src/Deleted.test.tsx:124 routes")
    )
    assert harness.apply_policy(criteria) == [
        "V01-P4-004: frontend evidence path does not exist: apps/ui/src/Deleted.test.tsx"
    ]


def test_every_recorded_frontend_pointer_lands_on_the_test_it_describes() -> None:
    """A line anchor that drifts reads as precision while proving nothing.

    Every ``path:line <title>`` segment of every ``frontend`` criterion in the real
    policy file must land on the opening line of a vitest ``test``/``it`` whose title is
    exactly the prose the evidence claims for it. Inserting or deleting a line above the
    anchor breaks this. Iterating the policy rather than a hardcoded id set means each
    criterion a later PR routes through vitest is held to the same standard on arrival.
    """

    import re
    import tomllib

    policy = tomllib.loads(harness.POLICY_PATH.read_text())["criteria"]
    frontend = {
        identifier: entry["evidence"]
        for identifier, entry in policy.items()
        if entry.get("verification") == "frontend"
    }
    # A pytest-proven criterion may also name the vitest spec carrying the rest of its
    # surface; that pointer is held to exactly the same standard.
    frontend.update(
        {
            identifier: entry["frontend_evidence"]
            for identifier, entry in policy.items()
            if entry.get("frontend_evidence")
        }
    )
    assert frontend, "the policy must keep at least one frontend criterion"

    checked = 0
    for identifier, evidence in frontend.items():
        for segment in evidence.split(" + "):
            pointer, _, described = segment.partition(" ")
            relative, _, line = pointer.partition(":")
            assert line, f"{identifier}: {pointer} carries no line anchor"
            assert described, f"{identifier}: {pointer} names no test title"
            source = (harness.ROOT / relative).read_text().splitlines()
            anchor = source[int(line) - 1]
            expected = re.compile(
                r"^\s*(?:test|it)\(\s*(['\"`])"
                + re.escape(described)
                + r"\1\s*,\s*(?:async\s*)?\(\s*\)\s*=>\s*\{"
            )
            assert expected.match(anchor), f"{identifier}: {pointer} lands on {anchor!r}"
            checked += 1
    assert checked >= len(frontend)


def test_a_pytest_proven_criterion_keeps_its_claim_while_its_vitest_pointer_is_checked(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``frontend_evidence`` must guard the page test without stealing the pytest proof.

    Differential on the pointer alone: a resolving pointer leaves the criterion proven
    by its passing pytest claim, and a deleted spec fails the policy even though the
    pytest claim is untouched.
    """

    def policy_file(pointer: str) -> object:
        path = tmp_path / f"criteria-{abs(hash(pointer))}.toml"  # type: ignore[operator]
        path.write_text(
            "[criteria]\n"
            f'AC3-UI-02 = {{ verification = "test", frontend_evidence = "{pointer}" }}\n'
        )
        return path

    def fresh() -> dict[str, object]:
        return {
            "AC3-UI-02": criterion(
                id="AC3-UI-02", release="v0.3", stage="M6", tests=["t::a"], outcomes=["passed"]
            )
        }

    good = fresh()
    monkeypatch.setattr(
        harness,
        "POLICY_PATH",
        policy_file(
            "apps/ui/src/App.test.tsx:132 navigates to the required operator screens"
        ),
    )
    assert harness.apply_policy(good) == []  # type: ignore[arg-type]
    assert harness.classify(good["AC3-UI-02"]) == "PROVEN"

    gone = fresh()
    monkeypatch.setattr(
        harness, "POLICY_PATH", policy_file("apps/ui/src/pages/Gone.test.tsx:1 connections")
    )
    assert harness.apply_policy(gone) == [  # type: ignore[arg-type]
        "AC3-UI-02: frontend evidence path does not exist: apps/ui/src/pages/Gone.test.tsx"
    ]


def test_a_misspelled_policy_key_is_rejected_rather_than_silently_dropped(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in a key name must fail the gate, not discard the value it carried.

    Differential on the key spelling alone: the correct key resolves its pointer and
    passes, while `frontend_evidenc` — which would otherwise drop every checked vitest
    pointer and still print PASS — is reported as an unknown key.
    """

    pointer = "apps/ui/src/App.test.tsx:132 navigates to the required operator screens"

    def policy_file(key: str) -> object:
        path = tmp_path / f"criteria-{key}.toml"  # type: ignore[operator]
        path.write_text(
            "[criteria]\n"
            f'AC3-UI-05 = {{ verification = "test", {key} = "{pointer}" }}\n'
        )
        return path

    def fresh() -> dict[str, object]:
        return {
            "AC3-UI-05": criterion(
                id="AC3-UI-05", release="v0.3", stage="M6", tests=["t::a"], outcomes=["passed"]
            )
        }

    spelled = fresh()
    monkeypatch.setattr(harness, "POLICY_PATH", policy_file("frontend_evidence"))
    assert harness.apply_policy(spelled) == []  # type: ignore[arg-type]
    assert spelled["AC3-UI-05"].frontend_evidence == pointer

    typo = fresh()
    monkeypatch.setattr(harness, "POLICY_PATH", policy_file("frontend_evidenc"))
    assert harness.apply_policy(typo) == [  # type: ignore[arg-type]
        "AC3-UI-05: unknown policy key 'frontend_evidenc'"
    ]
    assert typo["AC3-UI-05"].frontend_evidence == ""


def test_an_unknown_verification_mode_is_rejected(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`verification` outside the five documented literals must fail closed.

    Differential: `manual` with its required evidence passes; `manul` is reported,
    rather than being treated as an ordinary pytest claim that nothing checks.
    """

    def policy_file(mode: str) -> object:
        path = tmp_path / f"criteria-{mode}.toml"  # type: ignore[operator]
        path.write_text(
            "[criteria]\n"
            f'AC3-UI-04 = {{ verification = "{mode}", evidence = "operator walkthrough", '
            'last_verified = "2026-08-28" }\n'
        )
        return path

    def fresh() -> dict[str, object]:
        return {"AC3-UI-04": criterion(id="AC3-UI-04", release="v0.3", stage="M6")}

    monkeypatch.setattr(harness, "POLICY_PATH", policy_file("manual"))
    assert harness.apply_policy(fresh()) == []  # type: ignore[arg-type]

    monkeypatch.setattr(harness, "POLICY_PATH", policy_file("manul"))
    assert harness.apply_policy(fresh()) == [  # type: ignore[arg-type]
        "AC3-UI-04: unknown verification 'manul' "
        "(expected one of frontend, manual, not_yet_due, test, waived)"
    ]


# --- The M7 governance surface ----------------------------------------------


def test_the_seven_ema_rows_load_from_the_sdd_as_m7_musts() -> None:
    """§24.9 must reach the harness: seven ids, all MUST, all M7.

    Reads the criteria the gate itself builds, not the markdown, so a row that parses
    into the wrong stage (the ``unassigned`` fallback for an unmapped category) or a
    policy line that never landed fails here. A row leaves the deferred list exactly
    when the M7 PR proving it lands; with PR5 in, the deferred list is empty.
    """

    criteria = harness.load_criteria()
    assert harness.apply_policy(criteria) == []
    ema = {name: c for name, c in criteria.items() if name.startswith("AC3-EMA-")}

    assert sorted(ema) == [f"AC3-EMA-0{index}" for index in range(1, 8)]
    assert {c.stage for c in ema.values()} == {"M7"}
    assert {c.priority for c in ema.values()} == {"MUST"}
    # AC3-EMA-03 is claimed by tests/test_v03_m7_enterprise_auth.py, AC3-EMA-01 and
    # the session-ending half of -04 by tests/test_v03_m7_identity_retention.py,
    # -02, the revocation half of -04, -06 and -07 by
    # tests/test_v03_m7_enterprise_mcp.py, and -05 by
    # tests/test_v03_m7_enterprise_secret_scan.py. The deferred list is now empty:
    # every §24.9 row is in scope and claimed.
    claimed = tuple(f"AC3-EMA-0{index}" for index in range(1, 8))
    for name in claimed:
        assert ema[name].verification == "test"
        assert ema[name].in_scope
    deferred = [c for name, c in ema.items() if name not in claimed]
    assert deferred == []


def ci_gate_stages() -> list[str]:
    """Every ``--stage`` argument the acceptance job runs in CI, in order."""

    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    return re.findall(r"check_acceptance\.py --stage (\S+)", workflow)


def test_ci_runs_a_stage_gate_for_every_stage_the_criteria_declare() -> None:
    """A milestone whose criteria exist but whose gate is not in CI is ungated.

    Derived from the loaded criteria rather than a hard-coded list, so adding M8 rows
    without adding the CI line fails, and dropping the M7 line fails today.
    """

    declared = {c.stage for c in harness.load_criteria().values() if c.release == "v0.3"}
    gated = ci_gate_stages()

    assert "M7" in declared
    assert declared - set(gated) == set(), f"stages with no CI gate: {declared - set(gated)}"
    assert len(gated) == len(set(gated)), f"duplicate stage gates in CI: {gated}"


# --- The escape hatches must not be able to outlive or outrank a test -------


def test_a_waiver_whose_end_date_is_not_a_date_counts_as_expired() -> None:
    """`_expired` fails closed, so an unreadable end date never grants silence.

    Differential: a real ISO date in the future still classifies WAIVED, while
    `expires = "v0.4.0"` --- a release name no calendar can compare --- classifies
    WAIVER_EXPIRED instead of being waived forever.
    """

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    honest = criterion(
        verification="waived", reason="deferred", issue="#52", expires=tomorrow
    )
    assert harness.classify(honest) == "WAIVED"

    for abusive in ("v0.4.0", "next release", "2026-13-01", "soon"):
        entry = criterion(
            verification="waived", reason="deferred", issue="#52", expires=abusive
        )
        assert harness.classify(entry) == "WAIVER_EXPIRED", abusive
    assert harness._expired("v0.4.0") is True


def test_a_waiver_needs_an_iso_end_date_inside_the_180_day_horizon(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_policy` refuses a waiver that never really comes due.

    Differential: a waiver expiring in 30 days is accepted; a release name and a
    date ten years out are both reported as policy errors, because a waiver that
    outlives the release it was written for is a permanent grant with paperwork.
    """

    def policy_file(expires: str) -> object:
        path = tmp_path / f"criteria-waiver-{abs(hash(expires))}.toml"  # type: ignore[operator]
        path.write_text(
            "[criteria]\n"
            'AC3-UI-04 = { verification = "waived", reason = "deferred", '
            f'issue = "#52", expires = "{expires}" }}\n'
        )
        return path

    def fresh() -> dict[str, object]:
        return {"AC3-UI-04": criterion(id="AC3-UI-04", release="v0.3", stage="M6")}

    soon = (date.today() + timedelta(days=30)).isoformat()
    monkeypatch.setattr(harness, "POLICY_PATH", policy_file(soon))
    assert harness.apply_policy(fresh()) == []  # type: ignore[arg-type]

    monkeypatch.setattr(harness, "POLICY_PATH", policy_file("v0.4.0"))
    assert harness.apply_policy(fresh()) == [  # type: ignore[arg-type]
        "AC3-UI-04: waiver expires 'v0.4.0' is not an ISO-8601 date "
        "(YYYY-MM-DD); a release name never comes due"
    ]

    far = (date.today() + timedelta(days=3650)).isoformat()
    horizon = (date.today() + timedelta(days=harness.MAX_WAIVER_DAYS)).isoformat()
    monkeypatch.setattr(harness, "POLICY_PATH", policy_file(far))
    assert harness.apply_policy(fresh()) == [  # type: ignore[arg-type]
        f"AC3-UI-04: waiver expires {far} is more than 180 days out "
        f"(at most {horizon})"
    ]


def test_a_manual_or_waived_record_does_not_silence_its_own_failing_test() -> None:
    """A recorded belief must not outrank a test claiming the same criterion.

    Differential: with a passing claim the recorded mode still shows (MANUAL /
    WAIVED); the moment the claiming test fails, the criterion reports FAILING
    rather than hiding behind the record.
    """

    recent = (date.today() - timedelta(days=10)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    honest_manual = criterion(
        verification="manual",
        evidence="docs/releases/v0.3/evidence/live-acceptance.md",
        last_verified=recent,
        tests=["t::manual_guard"],
        outcomes=["passed"],
    )
    assert harness.classify(honest_manual) == "MANUAL"

    abusive_manual = criterion(
        verification="manual",
        evidence="docs/releases/v0.3/evidence/live-acceptance.md",
        last_verified=recent,
        tests=["t::manual_guard"],
        outcomes=["failed"],
    )
    assert harness.classify(abusive_manual) == "FAILING"

    honest_waiver = criterion(
        verification="waived",
        reason="deferred",
        issue="#52",
        expires=tomorrow,
        tests=["t::waiver_guard"],
        outcomes=["passed"],
    )
    assert harness.classify(honest_waiver) == "WAIVED"

    abusive_waiver = criterion(
        verification="waived",
        reason="deferred",
        issue="#52",
        expires=tomorrow,
        tests=["t::waiver_guard"],
        outcomes=["failed"],
    )
    assert harness.classify(abusive_waiver) == "FAILING"


def run_plugin_over(
    directory: Path, extra: list[str], suite: str | None = None
) -> dict[str, Any]:
    """Run a real pytest over a throwaway suite with the real AcceptancePlugin.

    Hand-rolled report objects would only prove that the plugin reads the fields
    this test decided to hand it; the phases and outcomes pytest actually files for
    a raising fixture are the thing under test, so a real pytest run produces them.
    A subprocess keeps that run out of the enclosing session.
    """

    (directory / "t_claims.py").write_text(suite if suite is not None else CLAIMING_SUITE)
    runner = directory / "run_plugin.py"
    runner.write_text(PLUGIN_RUNNER)
    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            str(directory / "t_claims.py"),
            "-p",
            "no:cacheprovider",
            "-q",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    results = [row for row in completed.stdout.splitlines() if row.startswith("RESULT ")]
    assert results, f"plugin run produced nothing:\n{completed.stdout}\n{completed.stderr}"
    return json.loads(results[-1][len("RESULT ") :])


PLUGIN_RUNNER = """
import json, sys
import pytest
from accretion.acceptance import AcceptancePlugin

plugin = AcceptancePlugin()
pytest.main(sys.argv[1:], plugins=[plugin])
print("RESULT " + json.dumps({"claims": dict(plugin.claims), "outcomes": plugin.outcomes}))
"""

CLAIMING_SUITE = '''
import pytest


@pytest.fixture
def broken_setup():
    raise RuntimeError("fixture blew up")


@pytest.fixture
def broken_teardown():
    yield "ok"
    raise RuntimeError("teardown blew up")


@pytest.mark.acceptance("FAKE-CLEAN")
def test_clean():
    assert True


@pytest.mark.acceptance("FAKE-SETUP")
def test_setup_error(broken_setup):
    assert True


@pytest.mark.acceptance("FAKE-TEARDOWN")
def test_teardown_error(broken_teardown):
    assert broken_teardown == "ok"


@pytest.mark.acceptance("FAKE-DESELECTED")
def test_never_runs():
    assert True
'''


def test_a_fixture_that_raises_is_recorded_as_an_error_and_classifies_failing(
    tmp_path: Path,
) -> None:
    """Setup and teardown failures must reach the gate rather than vanish.

    A fixture that raises files a ``setup`` report and no ``call`` report at all,
    and a teardown that raises files a ``call`` report saying ``passed`` --- so
    without this the first would leave no outcome behind and the second would read
    as clean evidence. Differential, inside one real pytest run: the honest test
    records ``passed`` and classifies PROVEN, while the broken-fixture and
    broken-teardown claims both record ``error`` and classify FAILING.
    """

    result = run_plugin_over(tmp_path, [])
    outcomes = result["outcomes"]

    assert outcomes["t_claims.py::test_clean"] == "passed"
    assert harness.classify(criterion(tests=["t::clean"], outcomes=["passed"])) == "PROVEN"

    assert outcomes["t_claims.py::test_setup_error"] == "error"
    assert outcomes["t_claims.py::test_teardown_error"] == "error"
    for node in ("t_claims.py::test_setup_error", "t_claims.py::test_teardown_error"):
        entry = criterion(tests=[node], outcomes=[outcomes[node]])
        assert harness.classify(entry) == "FAILING", node


def test_a_claim_that_never_reported_an_outcome_is_failing_not_proven(
    tmp_path: Path,
) -> None:
    """A collected claim with no outcome proves nothing.

    Differential: two claiming nodes that both passed are PROVEN; a criterion with
    one unreported node is FAILING. The unreported node is produced for real --- the
    throwaway suite is run under ``-k``, so ``FAKE-DESELECTED`` is claimed at
    collection and never reports --- and the CLI is what turns exactly that gap into
    the ``missing`` outcome.
    """

    honest = criterion(tests=["t::a", "t::b"], outcomes=["passed", "passed"])
    assert harness.classify(honest) == "PROVEN"

    result = run_plugin_over(tmp_path, ["-k", "not never_runs"])
    claimed = result["claims"]["FAKE-DESELECTED"]
    assert claimed == ["t_claims.py::test_never_runs"]
    assert claimed[0] not in result["outcomes"]
    assert result["outcomes"]["t_claims.py::test_clean"] == "passed"

    cli = (REPO_ROOT / "scripts" / "check_acceptance.py").read_text()
    assert 'plugin.outcomes.get(node, "missing")' in cli
    outcomes = [result["outcomes"].get(node, "missing") for node in claimed]
    assert outcomes == ["missing"]
    assert harness.classify(criterion(tests=claimed, outcomes=outcomes)) == "FAILING"


def test_a_skip_recorded_at_setup_still_classifies_skipped_only(tmp_path: Path) -> None:
    """Recording setup failures must not swallow the existing skip signal.

    A skip is also filed at ``setup`` and with no ``call`` report, so the branch that
    now records errors sits next to the one that records skips. Differential: the
    skipped claim still reads ``skipped`` and classifies SKIPPED_ONLY --- never
    ``error``, and never PROVEN.
    """

    suite = CLAIMING_SUITE + (
        '\n\n@pytest.mark.acceptance("FAKE-SKIPPED")\n'
        '@pytest.mark.skip(reason="live provider only")\n'
        "def test_skipped():\n    assert True\n"
    )
    result = run_plugin_over(tmp_path, [], suite=suite)
    node = result["claims"]["FAKE-SKIPPED"][0]
    assert result["outcomes"][node] == "skipped"
    assert harness.classify(criterion(tests=[node], outcomes=["skipped"])) == "SKIPPED_ONLY"
