"""The acceptance gate must be able to go red.

A harness that cannot fail is decoration, so every failure mode it claims to detect
is exercised here: a failing claim, an uncovered MUST, a skipped-only claim, an
expired waiver, and stale manual evidence.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

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
    assert "NOT_YET_DUE: 1" in result.stdout


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
    when the M7 PR proving it lands, so the split below moves as M7 is built.
    """

    criteria = harness.load_criteria()
    assert harness.apply_policy(criteria) == []
    ema = {name: c for name, c in criteria.items() if name.startswith("AC3-EMA-")}

    assert sorted(ema) == [f"AC3-EMA-0{index}" for index in range(1, 8)]
    assert {c.stage for c in ema.values()} == {"M7"}
    assert {c.priority for c in ema.values()} == {"MUST"}
    # AC3-EMA-03 is claimed by tests/test_v03_m7_enterprise_auth.py, AC3-EMA-01 and
    # the session-ending half of -04 by tests/test_v03_m7_identity_retention.py, and
    # -02, the revocation half of -04, -06 and -07 by
    # tests/test_v03_m7_enterprise_mcp.py; AC3-EMA-05 is still deferred, and
    # therefore out of scope and unclaimable.
    claimed = (
        "AC3-EMA-01",
        "AC3-EMA-02",
        "AC3-EMA-03",
        "AC3-EMA-04",
        "AC3-EMA-06",
        "AC3-EMA-07",
    )
    for name in claimed:
        assert ema[name].verification == "test"
        assert ema[name].in_scope
    deferred = [c for name, c in ema.items() if name not in claimed]
    assert {c.verification for c in deferred} == {"not_yet_due"}
    assert {harness.classify(c) for c in deferred} == {"NOT_YET_DUE"}
    assert not any(c.in_scope for c in deferred)


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
