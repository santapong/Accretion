"""Tests for ``scripts/sync_frontend_anchors.py`` (deliberately unmarked).

The tool rewrites `docs/acceptance/criteria.toml`, the file that decides which criteria
count as proven. Nothing it does is an SDD criterion, so nothing here claims one. What
these tests protect is its *refusal* to guess.

A tool that repairs acceptance evidence is only safe while it cannot invent evidence.
The whole guarantee rests on one rule -- it moves an address only when a test whose
title matches byte-for-byte already exists, and refuses otherwise -- so the refusals are
the behaviour worth pinning, not the happy path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ANCHOR = "the anchored test"
NEIGHBOUR = "the neighbouring test"


def load_sync():
    """Import scripts/sync_frontend_anchors.py, which is a script rather than a module."""

    spec = importlib.util.spec_from_file_location(
        "accretion_sync_frontend_anchors", ROOT / "scripts" / "sync_frontend_anchors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync = load_sync()


def spec_source(*titles: str) -> str:
    """A vitest spec declaring ``titles`` in order, one test every four lines."""

    body = 'import { expect, test } from "vitest";\n'
    for title in titles:
        body += f'\ntest("{title}", () => {{\n  expect(1).toBe(1);\n}});\n'
    return body


def tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    specs: dict[str, str],
    evidence: str,
    key: str = "evidence",
    verification: str = "frontend",
) -> Path:
    """Point the tool at a synthetic checkout and return its policy file."""

    for relative, source in specs.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    policy = tmp_path / "criteria.toml"
    policy.write_text(
        "[criteria]\n"
        f'V01-P4-004 = {{ verification = "{verification}", {key} = "{evidence}" }}\n'
    )
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    monkeypatch.setattr(sync, "POLICY_PATH", policy)
    return policy


def run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["sync_frontend_anchors.py", *argv])
    return sync.main()


# --- the addresses it will move ---------------------------------------------


def test_a_test_that_moved_down_is_readdressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case: a test added above pushes the anchored one down."""

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source(NEIGHBOUR, ANCHOR)},
        evidence=f"apps/ui/src/a.test.tsx:3 {ANCHOR}",
    )
    assert run(monkeypatch, "--write") == 0
    # NEIGHBOUR opens on 3, so ANCHOR is now on 7.
    assert f"apps/ui/src/a.test.tsx:7 {ANCHOR}" in policy.read_text()


def test_a_test_that_moved_to_another_file_is_readdressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restructure moves files, not just lines; the path is part of the address."""

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={
            "apps/ui/src/old.test.tsx": spec_source(NEIGHBOUR),
            "apps/ui/src/features/new.test.tsx": spec_source(ANCHOR),
        },
        evidence=f"apps/ui/src/old.test.tsx:3 {ANCHOR}",
    )
    assert run(monkeypatch, "--write") == 0
    assert f"apps/ui/src/features/new.test.tsx:3 {ANCHOR}" in policy.read_text()


def test_both_evidence_keys_are_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`frontend_evidence` on a pytest-proven criterion is checked by the gate too, so a
    tool that synced only `evidence` would leave half the pointers to rot."""

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source(NEIGHBOUR, ANCHOR)},
        evidence=f"apps/ui/src/a.test.tsx:3 {ANCHOR}",
        key="frontend_evidence",
        verification="test",
    )
    assert run(monkeypatch, "--write") == 0
    assert f"apps/ui/src/a.test.tsx:7 {ANCHOR}" in policy.read_text()


def test_every_pointer_in_a_multi_segment_claim_is_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real entries carry up to nine pointers joined by ` + `; one stale segment fails
    the gate just as hard as all of them."""

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source("filler", ANCHOR, NEIGHBOUR)},
        evidence=(
            f"apps/ui/src/a.test.tsx:3 {ANCHOR} + apps/ui/src/a.test.tsx:3 {NEIGHBOUR}"
        ),
    )
    assert run(monkeypatch, "--write") == 0
    updated = policy.read_text()
    assert f"apps/ui/src/a.test.tsx:7 {ANCHOR}" in updated
    assert f"apps/ui/src/a.test.tsx:11 {NEIGHBOUR}" in updated


# --- what it refuses to do --------------------------------------------------


def test_a_retitled_test_is_refused_rather_than_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing refusal.

    A title that no longer exists means either the test was renamed or its proof was
    deleted. Those are different facts with different consequences for the criterion,
    and no tool can tell them apart -- so it must not try. Re-pointing at whatever test
    happens to sit nearby is exactly how a criterion comes to cite something that does
    not prove it.
    """

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source("a completely different title")},
        evidence=f"apps/ui/src/a.test.tsx:3 {ANCHOR}",
    )
    before = policy.read_text()
    assert run(monkeypatch, "--write") == 1
    assert policy.read_text() == before


def test_an_ambiguous_title_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two tests sharing a title make the pointer meaningless: it names both and proves
    neither. Picking the first would silently choose one."""

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={
            "apps/ui/src/a.test.tsx": spec_source(ANCHOR),
            "apps/ui/src/b.test.tsx": spec_source(ANCHOR),
        },
        evidence=f"apps/ui/src/a.test.tsx:99 {ANCHOR}",
    )
    before = policy.read_text()
    assert run(monkeypatch, "--write") == 1
    assert policy.read_text() == before


def test_nothing_is_written_when_any_pointer_is_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All or nothing.

    Writing the resolvable pointers and refusing the rest would leave criteria.toml
    half-repaired, with no record of which half -- and the surviving stale pointer would
    read as deliberate.
    """

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source(NEIGHBOUR, ANCHOR)},
        evidence=(
            f"apps/ui/src/a.test.tsx:3 {ANCHOR} + apps/ui/src/a.test.tsx:9 a deleted test"
        ),
    )
    before = policy.read_text()
    assert run(monkeypatch, "--write") == 1
    # The resolvable half would have moved 3 -> 7. It must not have.
    assert policy.read_text() == before


def test_a_title_is_never_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety property, stated directly.

    The title is the claim and the address is only where it lives. If this tool could
    edit a title it could point a criterion at a different test's proof, which is the
    one thing the gate exists to prevent.
    """

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source(NEIGHBOUR, ANCHOR)},
        evidence=f"apps/ui/src/a.test.tsx:3 {ANCHOR}",
    )
    assert run(monkeypatch, "--write") == 0
    updated = policy.read_text()
    assert ANCHOR in updated
    assert NEIGHBOUR not in updated


# --- reporting --------------------------------------------------------------


def test_a_dry_run_reports_the_move_without_making_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source(NEIGHBOUR, ANCHOR)},
        evidence=f"apps/ui/src/a.test.tsx:3 {ANCHOR}",
    )
    before = policy.read_text()
    # Non-zero without --write, so a pre-commit hook or a human can use it as a check.
    assert run(monkeypatch) == 1
    assert policy.read_text() == before
    assert "apps/ui/src/a.test.tsx:3 -> apps/ui/src/a.test.tsx:7" in capsys.readouterr().out


def test_an_already_correct_policy_is_left_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running it on a healthy tree must be a no-op, including formatting and comments."""

    policy = tree(
        tmp_path,
        monkeypatch,
        specs={"apps/ui/src/a.test.tsx": spec_source(ANCHOR)},
        evidence=f"apps/ui/src/a.test.tsx:3 {ANCHOR}",
    )
    before = policy.read_text()
    assert run(monkeypatch, "--write") == 0
    assert policy.read_text() == before


def test_the_real_policy_needs_no_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The committed policy must already be in sync, so a move shows up as a real diff.

    Uses the true ROOT and POLICY_PATH, unlike every test above -- only `sys.argv` is
    stubbed, because `main` parses it and pytest's own arguments are not ours.
    """

    assert run(monkeypatch) == 0, (
        "docs/acceptance/criteria.toml has drifted; run `make anchors`"
    )
