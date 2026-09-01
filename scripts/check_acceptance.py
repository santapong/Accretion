"""Thin CLI over :mod:`accretion.acceptance`. Run with ``make acceptance``."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from accretion.acceptance import (
    _FAILING,
    Criterion,
    apply_policy,
    classify,
    load_criteria,
    run_tests,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify acceptance criteria coverage.")
    parser.add_argument(
        "--no-tests", action="store_true", help="report coverage without running the suite"
    )
    parser.add_argument("--stage", help="only report one phase or milestone, e.g. M2 or P3")
    parser.add_argument("--quiet", action="store_true", default=True)
    options = parser.parse_args()

    criteria = load_criteria()
    errors = apply_policy(criteria)

    if not options.no_tests:
        plugin = run_tests(options.quiet)
        for identifier, nodes in plugin.claims.items():
            criterion = criteria.get(identifier)
            if criterion is None:
                errors.append(f"{identifier}: claimed by a test but absent from every SDD")
                continue
            criterion.tests = nodes
            criterion.outcomes = [plugin.outcomes.get(node, "missing") for node in nodes]

    selected = [
        criterion
        for criterion in criteria.values()
        if not options.stage or criterion.stage == options.stage
    ]
    if options.stage and not selected:
        print(
            f"\nERROR: --stage {options.stage} selected no criteria. A stage that matches"
            "\nnothing is a typo or a milestone that does not exist yet; reporting PASS"
            "\nfor it would be a vacuous gate.",
            file=sys.stderr,
        )
        return 2

    statuses = {criterion.id: classify(criterion) for criterion in selected}

    by_status: dict[str, list[Criterion]] = defaultdict(list)
    for criterion in selected:
        by_status[statuses[criterion.id]].append(criterion)

    print("\n=== Acceptance criteria ===\n")
    for status in sorted(by_status):
        entries = by_status[status]
        print(f"{status}: {len(entries)}")
        if status in _FAILING or status == "MANUAL":
            for criterion in sorted(entries, key=lambda item: item.id):
                print(f"    {criterion.id:<14} [{criterion.stage:<3}] {criterion.text[:70]}")
        print()

    unmet = [
        criterion
        for criterion in selected
        if criterion.priority == "MUST" and statuses[criterion.id] in _FAILING
    ]
    in_scope = [criterion for criterion in selected if criterion.in_scope]
    proven = [c for c in in_scope if statuses[c.id] == "PROVEN"]
    print(f"in scope: {len(in_scope)}   proven: {len(proven)}   unmet MUST: {len(unmet)}")

    if errors:
        print("\npolicy errors:")
        for error in errors:
            print(f"    {error}")

    if errors or unmet:
        print("\nFAIL: every in-scope MUST criterion needs a passing claim, a current")
        print("manual record, or an unexpired waiver.")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
