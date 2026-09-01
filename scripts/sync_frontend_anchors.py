"""Re-address the vitest pointers in ``docs/acceptance/criteria.toml``.

``docs/acceptance/criteria.toml`` ties sixteen criteria to vitest tests by
``apps/ui/<path>.test.tsx:<line> <exact test title>``. The gate
(:func:`accretion.acceptance.frontend_evidence_errors`) checks the path, the line and
the title on every CI run, in two separate jobs, so a single line inserted above an
anchored test reddens the whole acceptance gate.

That check is worth keeping exactly as strict as it is. What it should not do is make
routine frontend work expensive: moving a test down four lines is not a change in what
the test proves, but today it costs a three-job CI round trip to discover and a manual
edit to repair.

**What this tool changes, and what it refuses to touch.** A pointer has two parts: a
*title*, which is the claim -- "this named test proves this criterion" -- and a
*path:line*, which is merely the address where that test currently lives. This tool
rewrites addresses. It never writes, edits or invents a title, never adds a pointer, and
never removes one. It cannot make a criterion look proven that is not, because the only
way it will move an address is by finding a test whose title already matches
byte-for-byte.

It therefore refuses, rather than guesses, in exactly the cases where a human has to
decide what happened:

* **no test carries the title** -- the test was retitled or deleted, and only a person
  can say whether the criterion still has a proof;
* **more than one test carries it** -- the address is genuinely ambiguous, and a pointer
  that could mean either test proves nothing.

Deliberately **not** wired into CI. A gate that repairs itself is not a gate: drift has
to be visible in a failing check, and the repair has to be a decision someone made.

Matching uses the stricter of the two anchors in the repository -- the one in
``tests/test_acceptance_harness.py::test_every_recorded_frontend_pointer_lands_on_the_test_it_describes``,
which also requires the line to end ``, () => {`` or ``, async () => {``. A pointer this
tool writes therefore satisfies both that test and the production gate; matching on the
looser production anchor alone could produce a pointer that passes the gate and still
fails pytest.

Usage::

    python scripts/sync_frontend_anchors.py            # report, change nothing
    python scripts/sync_frontend_anchors.py --write    # apply the new addresses
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

from accretion.acceptance import (
    _EVIDENCE_POINTER,
    _EVIDENCE_SEPARATOR,
    FRONTEND_EVIDENCE_ROOT,
    FRONTEND_EVIDENCE_SUFFIXES,
    POLICY_PATH,
    ROOT,
)

# Both keys carry pointers: `evidence` when verification is "frontend" (vitest is the
# only proof), and `frontend_evidence` on a criterion still proven by pytest whose
# claim covers only part of the surface.
EVIDENCE_KEYS = ("evidence", "frontend_evidence")


def strict_anchor(title: str) -> re.Pattern[str]:
    """The stricter of the repository's two anchors. See the module docstring."""

    return re.compile(
        r"^\s*(?:test|it)\(\s*(['\"`])"
        + re.escape(title)
        + r"\1\s*,\s*(?:async\s*)?\(\s*\)\s*=>\s*\{"
    )


def spec_files() -> list[Path]:
    """Every vitest spec the gate would accept a pointer into.

    ``ROOT`` is read here rather than captured at import so a test can point the whole
    tool at a synthetic tree, the same way the gate's own tests do.
    """

    ui_root = ROOT / FRONTEND_EVIDENCE_ROOT
    if not ui_root.is_dir():
        return []
    return sorted(
        path
        for path in ui_root.rglob("*")
        if path.is_file() and path.name.endswith(FRONTEND_EVIDENCE_SUFFIXES)
    )


def locate(title: str) -> list[tuple[str, int]]:
    """Every ``(relative path, 1-indexed line)`` whose line opens a test titled ``title``."""

    anchor = strict_anchor(title)
    found: list[tuple[str, int]] = []
    for path in spec_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if anchor.match(line):
                found.append((path.relative_to(ROOT).as_posix(), number))
    return found


def resync(identifier: str, evidence: str) -> tuple[str, list[str], list[str]]:
    """Return the re-addressed evidence string, the moves made, and any refusals."""

    moves: list[str] = []
    refusals: list[str] = []
    segments: list[str] = []
    for segment in evidence.split(_EVIDENCE_SEPARATOR):
        match = _EVIDENCE_POINTER.search(segment)
        if match is None:
            # Not a pointer at all. The gate reports this; leave it untouched so the
            # error a human needs to see is not quietly rewritten away.
            segments.append(segment)
            continue
        pointer = match.group(0)
        title = segment[match.end() :].strip()
        if not title:
            refusals.append(f"{identifier}: {pointer} names no test title")
            segments.append(segment)
            continue

        found = locate(title)
        if not found:
            refusals.append(
                f"{identifier}: no vitest test is titled {title!r}. "
                "It was retitled or deleted; decide what proves this criterion."
            )
            segments.append(segment)
            continue
        if len(found) > 1:
            where = ", ".join(f"{path}:{line}" for path, line in found)
            refusals.append(
                f"{identifier}: {len(found)} tests are titled {title!r} ({where}). "
                "An ambiguous pointer proves nothing; retitle one."
            )
            segments.append(segment)
            continue

        relative, line = found[0]
        current = f"{relative}:{line}"
        if current != pointer:
            moves.append(f"{identifier}: {pointer} -> {current}")
        segments.append(f"{current} {title}")
    return _EVIDENCE_SEPARATOR.join(segments), moves, refusals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help="apply the new addresses to criteria.toml"
    )
    options = parser.parse_args()

    raw = POLICY_PATH.read_text()
    policy = tomllib.loads(raw)["criteria"]

    moves: list[str] = []
    refusals: list[str] = []
    replacements: list[tuple[str, str]] = []
    for identifier, entry in sorted(policy.items()):
        for key in EVIDENCE_KEYS:
            evidence = entry.get(key)
            if not isinstance(evidence, str) or FRONTEND_EVIDENCE_ROOT not in evidence:
                continue
            updated, entry_moves, entry_refusals = resync(identifier, evidence)
            moves.extend(entry_moves)
            refusals.extend(entry_refusals)
            if updated != evidence:
                replacements.append((evidence, updated))

    for refusal in refusals:
        print(f"REFUSED  {refusal}")
    for move in moves:
        print(f"move     {move}")

    if refusals:
        # Refusals are not partial failures to work around: an unresolvable pointer
        # means the policy no longer describes reality, and writing the resolvable ones
        # would leave the file half-true with no record of which half.
        print(f"\nFAIL: {len(refusals)} pointer(s) need a human decision; nothing written.")
        return 1

    if not replacements:
        print("Every vitest pointer already names the line its test opens on.")
        return 0

    if not options.write:
        print(f"\n{len(moves)} pointer(s) would move. Re-run with --write to apply.")
        return 1

    for old, new in replacements:
        # Evidence strings are long and criterion-specific, so an exact single
        # replacement is safe -- and asserted, rather than assumed.
        if raw.count(old) != 1:
            print(f"FAIL: evidence string appears {raw.count(old)} times, expected once")
            return 1
        raw = raw.replace(old, new, 1)
    POLICY_PATH.write_text(raw)
    print(f"\nWrote {len(moves)} new address(es) to {POLICY_PATH.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
