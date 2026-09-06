"""Run the development-only §21 pilot over the router benchmark corpus.

Protocol §21 will not let the locked test begin until fifteen pre-registration fields are
frozen, and it says the numbers those fields are frozen from must come from a *development-
only* pilot. This is that pilot's command line: it loads a corpus, replays every §8.1
comparator over both halves of the split, and prints the variance, base rates, trial-to-trial
σ, power table and repeated-sampling measures that :mod:`accretion.routing.pilot` computes.

It decides nothing. No criterion is flipped, no threshold is written and no result is stored;
the output is evidence a human reads before editing
``docs/research/v0.4/preregistration.md`` by hand.

Two corpora, one command::

    # the shipped corpus, two trials per cell
    PYTHONPATH=src python scripts/router_pilot.py

    # a nine-trial regeneration from the same seed, written outside the repository
    PYTHONPATH=src python scripts/router_pilot.py --regenerate --trials 9 --out /tmp/pilot9

The second form exists because two trials per cell give one degree of freedom per cell, and a
σ estimated that thinly is a poor thing to size an experiment against. Regenerating at nine
trials from the **same seed** changes only the number of repeats, so the difference between
the two σ values is attributable to the repeat count and not to a different world.

``--out`` is mandatory with ``--regenerate`` and is refused if it points anywhere inside
``evals/``. The shipped corpus is frozen — a test asserts it is byte-for-byte what its seed
generates — and a pilot that could overwrite it in passing would be a pilot that could move
the baseline it was measuring.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT / "src"))

from accretion.router_benchmark import (  # noqa: E402
    CORPUS_ROOT,
    BenchmarkSplit,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
)
from accretion.routing.baselines import BASELINE_ORDER  # noqa: E402
from accretion.routing.pilot import PilotReport, pilot_report  # noqa: E402

EVALS_ROOT = ROOT / "evals"
"""The tree this command will not write into, under any flag combination."""

DEFAULT_REGENERATED_TRIALS = 9
"""OQ-409's floor of nine paired runs, which is also what §21 item 1 is proposed to freeze."""


def load_generator() -> ModuleType:
    """Import ``tests/router_corpus_generator.py``, the one place corpus bytes come from.

    The generator lives in ``tests/`` on purpose — the shipped package reads a corpus and
    never rebuilds one — so this script reaches into the test tree rather than the other way
    round. Importing it by the same bare module name pytest uses keeps one module identity in
    play, so a regeneration from this command and a regeneration from the corpus test cannot
    diverge.
    """

    tests_root = ROOT / "tests"
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    import router_corpus_generator

    return router_corpus_generator


@contextmanager
def trials_per_cell(generator: ModuleType, trials: int) -> Iterator[None]:
    """Rebind the generator's trial count for one build, then put it back.

    The generator publishes ``TRIALS_PER_CELL`` as a module constant and reads it inside the
    build, so this is the whole of what "regenerate at nine trials" means. Restoring it in a
    ``finally`` matters even in a single-shot CLI: the corpus test imports the same module,
    and a leaked constant would make a later assertion about the shipped corpus fail for a
    reason nowhere near the failure.
    """

    original = generator.TRIALS_PER_CELL
    generator.TRIALS_PER_CELL = trials
    try:
        yield
    finally:
        generator.TRIALS_PER_CELL = original


def regenerate(out: Path, trials: int) -> Path:
    """Write a ``trials``-per-cell corpus from the shipped seed into ``out``, and return it.

    Refuses any destination inside ``evals/``, resolved first so that a symlink or a
    ``../`` cannot walk back in. The refusal is a ``ValueError`` and not a warning: the
    shipped corpus is the benchmark's frozen input, and "the pilot overwrote it" is not a
    failure mode worth leaving open to a typo.
    """

    if trials < 2:
        raise ValueError(f"a regenerated corpus needs at least two trials per cell; got {trials}")
    destination = out.resolve()
    if destination == EVALS_ROOT or EVALS_ROOT in destination.parents:
        raise ValueError(
            f"{destination} is inside {EVALS_ROOT}; the shipped corpus is frozen and the "
            "pilot never writes into it"
        )
    destination.mkdir(parents=True, exist_ok=True)
    generator = load_generator()
    with trials_per_cell(generator, trials):
        generator.write(destination)
    return destination


def reports(corpus: RouterBenchmarkCorpus, alpha: float) -> list[PilotReport]:
    """One pilot report per half of the split, selection first.

    Both halves are reported because the pilot is development-only: the selection half is the
    side a developer may look at while iterating, and the evaluation half is quoted here as a
    *variance* measurement rather than as a result. Printing the two side by side is also the
    cheapest check that the halves are comparable populations, which the corpus generator
    claims by construction and nobody has otherwise verified.
    """

    runner = RouterBenchmarkRunner(corpus)
    return [
        pilot_report(runner, list(BASELINE_ORDER), split=split, alpha=alpha)
        for split in (BenchmarkSplit.SELECTION, BenchmarkSplit.EVALUATION)
    ]


def _interval(bounds: tuple[float, float]) -> str:
    return f"[{bounds[0]:.4f}, {bounds[1]:.4f}]"


def render(report: PilotReport) -> str:
    """The human-readable rendering of one report: the corpus, the σ, then a row per policy."""

    lines = [
        f"== {report.split.value} half - {report.tasks} tasks, {report.projects} projects, "
        f"{report.candidates} configurations",
        f"   run {report.run_id}  corpus {report.corpus_sha256[:12]}  "
        f"traces {report.trace_sha256[:12]}  seed {report.seed}  alpha {report.alpha}",
        f"   trial sigma (utility)   {report.utility_sigma.sigma:.6f}  "
        f"cells {report.utility_sigma.cells}  df {report.utility_sigma.degrees_of_freedom}  "
        f"min trials {report.utility_sigma.minimum_trials_per_cell}"
        f"{'  FLAGGED: <=2 trials per cell' if report.utility_sigma.flagged else ''}",
        f"   trial sigma (pass rate) {report.pass_rate_sigma.sigma:.6f}  "
        f"cells {report.pass_rate_sigma.cells}  df {report.pass_rate_sigma.degrees_of_freedom}  "
        f"min trials {report.pass_rate_sigma.minimum_trials_per_cell}"
        f"{'  FLAGGED: <=2 trials per cell' if report.pass_rate_sigma.flagged else ''}",
    ]
    for label, table in (
        ("utility", report.utility_power),
        ("pass rate", report.pass_rate_power),
    ):
        sizes = "  ".join(
            f"delta {row.delta:.2f} -> n {row.runs_per_configuration}" for row in table.rows
        )
        lines.append(
            f"   power ({label}, alpha {table.alpha}, power {table.power}, "
            f"sigma {table.sigma:.6f}): {sizes}"
        )
    lines.append(
        f"   {'policy':<7} {'regret':>9} {'within':>9} {'between':>9} {'ICC':>7} "
        f"{'verified':>9} {'CP interval':>19} {'FA':>3} {'FA<=':>8} {'outage':>7} "
        f"{'pass@k':>7} {'pass^k':>7}"
    )
    for line in report.policies:
        if not line.available or line.rates is None or line.regret_variance is None:
            lines.append(f"   {line.policy_id:<7} {'-':>9} unavailable ({line.reason_code})")
            continue
        lines.append(
            f"   {line.policy_id:<7} {line.mean_regret:>9.4f} "
            f"{line.regret_variance.within:>9.4f} {line.regret_variance.between:>9.4f} "
            f"{line.regret_icc if line.regret_icc is not None else 0.0:>7.3f} "
            f"{line.rates.verified_success_rate:>9.4f} "
            f"{_interval(line.rates.verified_success_interval):>19} "
            f"{line.rates.false_acceptances:>3} "
            f"{line.rates.false_acceptance_upper:>8.4f} {line.outage_rate:>7.4f} "
            f"{line.mean_pass_at_k if line.mean_pass_at_k is not None else 0.0:>7.4f} "
            f"{line.pass_pow_k if line.pass_pow_k is not None else 0.0:>7.4f}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """The command line. ``--out`` is required by ``--regenerate`` and checked in code."""

    parser = argparse.ArgumentParser(
        prog="router_pilot",
        description="Development-only pilot statistics for the v0.4 router benchmark (§21).",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS_ROOT,
        help="corpus directory to measure (default: the shipped evals/router corpus)",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="rebuild a corpus from the shipped seed into --out and measure that instead",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=DEFAULT_REGENERATED_TRIALS,
        help=f"trials per cell for --regenerate (default: {DEFAULT_REGENERATED_TRIALS})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where --regenerate writes; must be outside evals/",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="two-sided level for every reported interval (default: 0.05)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the reports as one JSON document instead of the table",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Measure one corpus and print it. Returns 0 unless the corpus refuses to load."""

    args = build_parser().parse_args(argv)
    root = args.corpus
    trials: int | None = None
    if args.regenerate:
        if args.out is None:
            print(
                "--regenerate requires --out (and --out may not be inside evals/)",
                file=sys.stderr,
            )
            return 2
        root = regenerate(args.out, args.trials)
        trials = args.trials
    corpus = RouterBenchmarkCorpus.load(root)
    measured = reports(corpus, args.alpha)
    if args.json:
        document: dict[str, Any] = {
            "corpus_root": str(root),
            "regenerated": args.regenerate,
            "trials_per_cell": trials,
            "reports": [report.model_dump(mode="json") for report in measured],
        }
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0
    print(f"router benchmark pilot - corpus {root}")
    for report in measured:
        print(render(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - a maintenance entry point, not a test path
    raise SystemExit(main())
