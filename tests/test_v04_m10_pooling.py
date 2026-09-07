"""The registered pooling rule: two readings of one cell, and a gate that says which it used.

ADR4-M10-005 recorded a benchmark that fails both safety gates for a reason that is not about
any router. A cell holds eighteen trials, the corpus reduces them with a conjunction — verified
means verified on *every* trial, a false acceptance on *any* trial is a false acceptance — and
the two thresholds were registered against the per-trial scale. On the locked corpus the same
rows read 0.5121 as a rate and 0.0870 as a conjunction, and 0.0390 as a rate against 0.4348 as
a disjunction. The fix the ADR names is a pooling rule that is registered rather than assumed.

Three claims, and the first of them is the expensive one.

**Nothing moved.** A corpus that registers no rule reports exactly the gates it reported at
v0.4.0. The goldens below were taken from the shipped corpus *before* this change existed, so
they are a record of the old behaviour and not a photograph of the new one — the difference
matters, because a golden captured after a refactor proves only that the refactor is
self-consistent. Every shipped corpus is in that position: none of them names a rule, so none of
their numbers, digests or run ids may move, and `docs/research/v0.4/results.md` stays a page
about the run that produced it.

**The rate reading is real and it is the corpus's decision.** A copied corpus whose cells are
hand-built — eighteen trials, nine of them verified, exactly one of them a false acceptance —
reports 0.0 and 1.0 under the conjunction and 0.5 and 1/18 under the rate rule, from the same
traces and against the same untouched floor and ceiling. That is the degeneracy the ADR
describes, reproduced at a size small enough to read: the conservative reading turns a coin flip
into a failure and a single wrong acceptance into a certainty. Both gates still fail here — one
in eighteen is 0.0556 and the ceiling is 0.05 — because a reading is not a way to pass a
threshold, and a test that showed a rule turning a red gate green would be advertising exactly
the thing a pre-registration exists to forbid.

**The two gates are two decisions.** A corpus may register the rate reading for false acceptance
and keep the conjunction for verified success. They are separate criteria in the type system
because they are separate criteria in the protocol, and a rule that moved them together would be
a third choice nobody registered.

The amendment that proposes using the rate reading for the v0.4 corpora is
`docs/research/v0.4/amendment-1.md`, and it is a draft: no locked corpus is read here, and no
threshold changes anywhere in this file.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from accretion.router_benchmark import (
    GateReport,
    PoolingRule,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
)

CORPUS_FILES = (
    "config.v1.json",
    "tasks.v1.json",
    "candidates.v1.json",
    "replay-traces.v1.json",
    "projects.v1.json",
)

FROZEN_GATES = {
    "M0": GateReport(
        selections=18,
        verified_successes=6,
        verified_success_rate=0.333333333,
        verified_success_floor=0.7,
        verified_success_met=False,
        false_acceptances=2,
        false_acceptance_rate=0.111111111,
        false_acceptance_ceiling=0.05,
        false_acceptance_met=False,
        pooling=PoolingRule(),
    ),
    "M1": GateReport(
        selections=18,
        verified_successes=6,
        verified_success_rate=0.333333333,
        verified_success_floor=0.7,
        verified_success_met=False,
        false_acceptances=0,
        false_acceptance_rate=0.0,
        false_acceptance_ceiling=0.05,
        false_acceptance_met=True,
        pooling=PoolingRule(),
    ),
    "M5": GateReport(
        selections=18,
        verified_successes=8,
        verified_success_rate=0.444444444,
        verified_success_floor=0.7,
        verified_success_met=False,
        false_acceptances=2,
        false_acceptance_rate=0.111111111,
        false_acceptance_ceiling=0.05,
        false_acceptance_met=False,
    ),
}
"""What the shipped corpus's gates were at v0.4.0, recorded before the rule existed.

``M5``'s golden deliberately omits ``pooling`` and takes the field's default: the default
*is* the frozen conjunction, and a test that named the rule in every golden could not tell a
defaulted field from a computed one.
"""

TRIALS_PER_CELL = 18
"""Pre-registration item 1's frozen size, which is the size the conjunction degenerates at."""

VERIFIED_TRIALS = 9
"""Nine of eighteen: a rate of exactly 0.5 and a conjunction of ``False``."""

FALSE_ACCEPT_TRIALS = 1
"""One of eighteen: a rate of 0.055555556 and a disjunction of ``True``."""


def setup_hand_pooled_corpus(root: Path, *, pooling: dict[str, str] | None) -> Path:
    """The shipped corpus with hand-built cells, and optionally a registered pooling rule.

    The tasks, the projects and the split are the shipped ones, so the corpus loads under every
    check :meth:`RouterBenchmarkCorpus.load` makes. Two documents are rewritten. Every
    configuration is declared eligible for every node class, which makes every cell admissible
    and the gate's denominator the same eighteen selections whichever configuration a policy
    picks — the point here is the *reading* of a cell, and a corpus where some selections were
    refused would let an invalid choice change the numbers instead. And every cell is rebuilt at
    eighteen identical trials, so the two readings of the corpus are a single pair of numbers a
    reader can check by hand rather than a distribution.
    """

    root.mkdir(parents=True)
    shipped = RouterBenchmarkCorpus.load().root
    for name in CORPUS_FILES:
        shutil.copy(shipped / name, root / name)

    tasks = _document(root / "tasks.v1.json")["tasks"]
    node_classes = sorted({task["node_class"] for task in tasks})
    candidates = _document(root / "candidates.v1.json")
    for candidate in candidates["candidates"]:
        candidate["eligible_node_classes"] = node_classes
    _write(root / "candidates.v1.json", candidates)

    traces = [
        {
            "task_id": task["task_id"],
            "candidate_id": candidate["candidate_id"],
            "trial": trial,
            "quality": 0.5,
            "cost": 0.2,
            "latency_ms": 1000,
            "verified": trial < VERIFIED_TRIALS,
            "false_accept": trial < FALSE_ACCEPT_TRIALS,
            "invalid": False,
        }
        for task in tasks
        for candidate in candidates["candidates"]
        for trial in range(TRIALS_PER_CELL)
    ]
    _write(root / "replay-traces.v1.json", {"suite_version": "v1", "traces": traces})

    config = _document(root / "config.v1.json")
    if pooling is not None:
        config["pooling"] = pooling
    _write(root / "config.v1.json", config)
    return root


def _document(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def gates_for(root: Path) -> GateReport:
    """The evaluation-half gates one corpus produces for the deterministic v0.1 policy.

    ``M0`` reads the corpus's registered configuration table and learns nothing, so the
    selections are a property of the corpus alone and any movement in the report is movement in
    the gate rather than in a policy that saw different evidence.
    """

    gates = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root)).run(["M0"]).policy("M0").gates
    assert gates is not None
    return gates


def test_a_corpus_that_registers_no_rule_reports_the_v040_gates_unchanged() -> None:
    corpus = RouterBenchmarkCorpus.load()
    assert corpus.config.pooling is None, "the shipped corpus registers no rule and must not"

    result = RouterBenchmarkRunner(corpus).run(sorted(FROZEN_GATES))
    for policy_id, golden in FROZEN_GATES.items():
        assert result.policy(policy_id).gates == golden, (
            f"{policy_id}'s gates moved against the v0.4.0 goldens; the absent rule must be "
            "the conjunction the shipped corpora were measured under"
        )
        report = result.policy(policy_id).gates
        assert report is not None
        assert report.pooling == PoolingRule(verified="all", false_accept="any")


def test_the_rate_rule_reads_the_per_trial_fractions_the_conjunction_hides(tmp_path: Path) -> None:
    conjunction = gates_for(setup_hand_pooled_corpus(tmp_path / "frozen", pooling=None))
    rated = gates_for(
        setup_hand_pooled_corpus(
            tmp_path / "rated", pooling={"verified": "rate", "false_accept": "rate"}
        )
    )

    # The same eighteen selections over the same traces, read two ways.
    assert conjunction.selections == rated.selections == 18
    assert conjunction.verified_success_rate == 0.0, "nine of eighteen is not all of eighteen"
    assert conjunction.false_acceptance_rate == 1.0, "one of eighteen is some of eighteen"
    assert conjunction.verified_successes == 0
    assert conjunction.false_acceptances == 18

    assert rated.verified_success_rate == 0.5
    assert rated.false_acceptance_rate == round(FALSE_ACCEPT_TRIALS / TRIALS_PER_CELL, 9)
    assert rated.verified_successes == 9
    assert rated.false_acceptances == 1

    # Not one threshold moved, and the report names the rule that produced it.
    assert rated.verified_success_floor == conjunction.verified_success_floor == 0.7
    assert rated.false_acceptance_ceiling == conjunction.false_acceptance_ceiling == 0.05
    assert rated.pooling == PoolingRule(verified="rate", false_accept="rate")
    assert conjunction.pooling == PoolingRule()

    # Both gates still fail on this corpus, and that is the honest half of the story: a
    # pooling rule is not a way to clear a threshold. What changes is the *distance*. One
    # wrong acceptance in eighteen reads as 1.0 against a ceiling of 0.05 under the
    # disjunction and as 0.055555556 under the rate — twenty times closer to a ceiling it
    # still misses, on rows nobody edited.
    assert conjunction.false_acceptance_met is False
    assert rated.false_acceptance_met is False
    assert rated.false_acceptance_rate < conjunction.false_acceptance_rate
    assert conjunction.verified_success_met is False
    assert rated.verified_success_met is False


def test_a_corpus_may_register_the_rate_reading_for_one_gate_and_not_the_other(
    tmp_path: Path,
) -> None:
    rated_acceptance = gates_for(
        setup_hand_pooled_corpus(tmp_path / "one", pooling={"false_accept": "rate"})
    )

    assert rated_acceptance.pooling == PoolingRule(verified="all", false_accept="rate")
    assert rated_acceptance.false_acceptance_rate == round(
        FALSE_ACCEPT_TRIALS / TRIALS_PER_CELL, 9
    )
    assert rated_acceptance.verified_success_rate == 0.0, (
        "the verified gate was not asked to change reading and must still be the conjunction"
    )


def test_the_two_readings_disagree_in_the_direction_the_adr_recorded() -> None:
    corpus = RouterBenchmarkCorpus.load()
    cells = corpus.pooled_cells()

    for pair, outcome in cells.items():
        assert outcome.verified_rate >= float(outcome.verified), (
            f"cell {pair} verified on every trial but reports a rate below one; the "
            "conjunction can never exceed the fraction it is a conjunction over"
        )
        assert outcome.false_accept_rate <= float(outcome.false_accept), (
            f"cell {pair} reports a false-acceptance rate above its disjunction; a rate over "
            "trials can never exceed 'it happened at least once'"
        )

    # And the inequalities are strict somewhere, or the two readings would be the same reading
    # and ADR4-M10-005 would have had nothing to record.
    assert any(not outcome.verified and outcome.verified_rate > 0.0 for outcome in cells.values())
    assert any(
        outcome.false_accept and outcome.false_accept_rate < 1.0 for outcome in cells.values()
    )

    # A cell of one trial is its own rate: the readings only part company over several trials,
    # which is why pooling two of them looked harmless and pooling eighteen did not.
    for pair, outcome in corpus.first_trial_cells().items():
        assert outcome.verified_rate == float(outcome.verified), pair
        assert outcome.false_accept_rate == float(outcome.false_accept), pair
