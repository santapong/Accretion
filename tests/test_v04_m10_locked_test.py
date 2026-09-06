"""The locked test set: the three refusals, the eleven comparators, and the claim not made.

This file is where v0.4 either earns its research claim or declines to make one, so it is
organised around what could go wrong rather than around the code.

**The door (AC4-M10-045).** ``evals/router/locked`` and ``evals/router/drift`` are committed
files, so nothing physical stops a process reading them. What stops it is
:class:`~accretion.routing.locked_test.LockedTestRunner`: the pre-registration on disk must
still hash to the digest the corpus pinned, ``ACCRETION_ROUTER_LOCKED_TEST`` must be ``1``, no
training snapshot handed in may name a locked project, and every released read appends a row to
a committed log. Each of the four is tested for what it *refuses* and, separately, for what it
still allows — a guard that refused everything would pass a refusal test and be useless.

**The field (AC4-M10-046).** All eleven protocol §8.1 comparators must actually run on the
locked evaluation half. Availability is asserted policy by policy rather than by counting,
because a count of eleven is also what ten runnable methods and one duplicated row produce.

**The claim (AC4-M10-050).** Three ways to announce a result that is not there, each with its
own test: a router that *is* the baseline (no gain to find), a pre-registration edited after the
rows were seen (a protocol chosen to fit), and a difference that clears α but not α/(K+L) (a
family counted wrong). Each refusal is paired with the positive case that keeps it from being
vacuous — a recovered fraction *is* reported when the opportunity gap's lower limit is positive,
and a difference that clears the adjusted level *is* significant.

**The page.** ``docs/research/v0.4/results.md`` is generated, not typed: the last test here
regenerates every block from the committed corpora and requires the page's fenced blocks to
match exactly. A number in that page that nobody can reproduce cannot survive this file.

Nothing here writes to the committed access log. Every runner a test builds is pointed at a log
in a temporary directory, because the row count of ``docs/research/v0.4/access-log.jsonl`` is an
audited quantity and a suite that appended to it on every run would make the audit meaningless.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import pytest

from accretion.config import Settings
from accretion.contracts import PrincipalRef, PrincipalStatus
from accretion.contracts.refs import PolicyRef
from accretion.contracts.routing import (
    PermissionProvenance,
    RouterTrainingSnapshot,
    SnapshotSplit,
    Visibility,
)
from accretion.ids import new_id
from accretion.router_benchmark import (
    CORPUS_ROOT,
    REPOSITORY_ROOT,
    BenchmarkSplit,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
)
from accretion.routing.baselines import BASELINE_ORDER
from accretion.routing.locked_test import (
    ABLATION_IDS,
    ACCESS_LOG_PATH,
    DRIFT_CORPUS_ROOT,
    LOCKED_CORPUS_ROOT,
    PREREGISTRATION_PATH,
    PRIMARY_POLICY_ID,
    LockedTestNotReleased,
    LockedTestRead,
    LockedTestRefused,
    LockedTestRunner,
    PreregistrationDrift,
    append_access_row,
    assert_no_training_overlap,
    block_key,
    preregistration_digest,
    read_access_log,
    render_blocks,
)
from accretion.routing.split import SplitViolation
from accretion.routing.stats import bonferroni, clopper_pearson, estimands, paired_regret_ci

RESULTS_PATH = REPOSITORY_ROOT / "docs" / "research" / "v0.4" / "results.md"

READER = "usr-router-locked-test"
REASON = "a test of the locked-test door"
AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

RELEASED = Settings(router_locked_test=True)
"""Settings with the flag up, injected rather than exported.

The environment path is proven once, by
``test_the_release_flag_alone_decides_whether_the_locked_set_may_be_read``. Every other test
injects, so that a leaked environment variable cannot make a refusal test pass by accident and
the tests do not have to run in a particular order."""


def setup_runner(
    tmp_path: Path,
    corpus_root: Path = LOCKED_CORPUS_ROOT,
    *,
    settings: Settings = RELEASED,
    preregistration_path: Path = PREREGISTRATION_PATH,
) -> tuple[LockedTestRunner, Path]:
    """A runner whose access log is a file in ``tmp_path``, and that file's path."""

    log = tmp_path / "access-log.jsonl"
    runner = LockedTestRunner(
        corpus_root,
        preregistration_path=preregistration_path,
        access_log_path=log,
        settings=settings,
    )
    return runner, log


def setup_snapshot(*, training: list[str], validation: list[str], holdout: list[str]) -> (
    RouterTrainingSnapshot
):
    """One §10.1 training snapshot with the three project groups a caller wants to test.

    Built through ``model_validate`` for the reason
    :mod:`accretion.routing.training_snapshot` gives: the pydantic mypy plugin does not carry
    ``CanonicalContract``'s header fields onto a subclass declared elsewhere, and a blanket
    ``type: ignore[call-arg]`` would hide a misspelled field as well as the header ones.
    """

    principal = PrincipalRef(
        principal_id="usr-snapshot", display_name="snapshot", status=PrincipalStatus.ACTIVE
    )
    return RouterTrainingSnapshot.model_validate(
        {
            "contract_id": new_id("router_training_snapshot"),
            "created_at": AT,
            "created_by": principal,
            "workspace_id": "wks-router-benchmark",
            "included_experience_ids": ["exp-router-locked-test"],
            "permission_proof": PermissionProvenance(
                scope=Visibility.PROJECT,
                policy=PolicyRef(policy_id="pol-router", version="1", content_digest="0" * 64),
                granted_by=principal,
                justification="a snapshot built for the overlap check",
            ),
            "contradiction_treatment": "open contradictions excluded",
            "deduplication_rule": "one row per experience",
            "window_start": datetime(2026, 1, 1, tzinfo=UTC),
            "window_end": datetime(2026, 9, 1, tzinfo=UTC),
            "split": SnapshotSplit(
                training_project_ids=training,
                validation_project_ids=validation,
                holdout_project_ids=holdout,
            ),
        }
    )


@lru_cache(maxsize=1)
def setup_release_reads() -> tuple[LockedTestRead, LockedTestRead, Path]:
    """The two released reads ``results.md`` quotes, performed once for the whole module.

    Cached because the pair costs about six seconds — eleven comparators and ten ablations over
    3,888 traces, twice — and every assertion below is about the same two reads. The log is a
    fresh temporary file, so the count it ends up with is a property of this function and not of
    whatever else ran first.
    """

    log = Path(tempfile.mkdtemp(prefix="accretion-locked-test-")) / "access-log.jsonl"
    reads = []
    for root in (LOCKED_CORPUS_ROOT, DRIFT_CORPUS_ROOT):
        runner = LockedTestRunner(root, access_log_path=log, settings=RELEASED)
        reads.append(
            runner.run(
                list(BASELINE_ORDER),
                principal=READER,
                reason=f"{REASON} ({root.name})",
                accessed_at=AT,
                ablation_ids=ABLATION_IDS,
            )
        )
    return reads[0], reads[1], log


# --------------------------------------------------------------------------------------
# AC4-M10-045 — the door, the overlap check and the log.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M10-045")
def test_a_snapshot_that_names_a_locked_project_stops_the_read_in_any_group(
    tmp_path: Path,
) -> None:
    """A fitted router may not have seen these projects — in training, validation *or* holdout.

    All three groups are refused, and the mutation the check exists for is the one that only
    guards ``training``: a snapshot that sealed a locked project into its holdout was still
    built from a registry containing it, and the next cut of that registry is one seed away from
    putting it in training.
    """

    runner, log = setup_runner(tmp_path)
    locked = sorted(
        RouterBenchmarkCorpus.load(LOCKED_CORPUS_ROOT).config.selection_split.evaluation_project_ids
    )
    leaked = locked[0]
    development = ["prj-router-web-api", "prj-router-cli-scaffold", "prj-router-docs-site"]

    for group in ("training", "validation", "holdout"):
        groups = {
            "training": [development[0]],
            "validation": [development[1]],
            "holdout": [development[2]],
        }
        groups[group] = [*groups[group], leaked]
        with pytest.raises(SplitViolation) as error:
            runner.run(
                ["M0"],
                principal=READER,
                reason=REASON,
                accessed_at=AT,
                snapshots=[setup_snapshot(**groups)],  # type: ignore[arg-type]
            )
        assert leaked in str(error.value)
        assert f"split.{group}" in str(error.value)

    # A refused read is not a read: nothing was appended, so the log cannot be padded by
    # attempts that were turned away.
    assert read_access_log(log) == ()

    # And the same runner reads happily when every named project belongs to the development
    # registry, which is what stops this being a guard that refuses everything.
    clean = setup_snapshot(
        training=[development[0]], validation=[development[1]], holdout=[development[2]]
    )
    read = runner.run(
        ["M0"], principal=READER, reason=REASON, accessed_at=AT, snapshots=[clean]
    )
    assert read.policy("M0").available
    assert len(read_access_log(log)) == 1


def test_the_overlap_check_reports_every_offending_group_at_once() -> None:
    """Two leaks in one snapshot are two lines in one error, not two runs to discover them."""

    with pytest.raises(SplitViolation) as error:
        assert_no_training_overlap(
            [
                setup_snapshot(
                    training=["prj-router-locked-web-api"],
                    validation=["prj-router-cli-scaffold"],
                    holdout=["prj-router-drift-docs-site"],
                )
            ],
            project_ids=["prj-router-locked-web-api", "prj-router-drift-docs-site"],
        )
    message = str(error.value)
    assert "snapshot[0].split.training" in message
    assert "snapshot[0].split.holdout" in message
    assert "validation" not in message

    # Nothing at all is raised when the snapshot names no locked project, and an empty
    # iterable of snapshots is not a leak.
    assert_no_training_overlap([], project_ids=["prj-router-locked-web-api"])
    assert_no_training_overlap(
        [setup_snapshot(training=["prj-a"], validation=[], holdout=["prj-b"])],
        project_ids=["prj-router-locked-web-api"],
    )


def test_one_released_read_appends_exactly_one_row_and_the_next_appends_one_more(
    tmp_path: Path,
) -> None:
    """The log grows by one per read, in the order the reads happened, and never rewrites."""

    runner, log = setup_runner(tmp_path)
    assert read_access_log(log) == ()

    first = runner.run(["M0"], principal=READER, reason="the first read", accessed_at=AT)
    rows = read_access_log(log)
    assert len(rows) == 1
    assert rows[0] == {
        "schema_version": "1.0",
        "principal": READER,
        "protocol_digest": preregistration_digest(),
        "accessed_at": AT.isoformat(),
        "reason": "the first read",
    }
    assert first.entry.reason == "the first read"

    later = AT.replace(hour=13)
    runner.run(["M0"], principal="usr-second", reason="the second read", accessed_at=later)
    rows = read_access_log(log)
    assert len(rows) == 2
    assert rows[0]["reason"] == "the first read"
    assert [row["principal"] for row in rows] == [READER, "usr-second"]
    assert [row["accessed_at"] for row in rows] == [AT.isoformat(), later.isoformat()]

    # In-memory and on disk agree, and the in-memory log is append-only by shape.
    assert [entry.reason for entry in runner.log.entries] == ["the first read", "the second read"]
    assert runner.log.to_rows() == [dict(row) for row in rows]


def test_the_development_corpus_is_not_readable_through_the_locked_door(tmp_path: Path) -> None:
    """The shipped corpus runs with no guard and leaves no row; it cannot be read as locked.

    Both halves matter. If ``evals/router`` could be read through this runner it would put rows
    in the access log that mean nothing, and the audited row count would stop being evidence;
    if it could *not* be read without the runner, every development test would need the release
    flag and the flag would become something everyone exports by default.
    """

    with pytest.raises(LockedTestRefused, match="is not a locked corpus"):
        LockedTestRunner(CORPUS_ROOT, access_log_path=tmp_path / "log.jsonl", settings=RELEASED)

    result = RouterBenchmarkRunner().run(["M0", PRIMARY_POLICY_ID])
    assert result.policy(PRIMARY_POLICY_ID).available
    assert not (tmp_path / "log.jsonl").exists()
    # The drift holdout, unlike the development corpus, is a locked corpus and is accepted.
    LockedTestRunner(DRIFT_CORPUS_ROOT, access_log_path=tmp_path / "log.jsonl")


def test_a_hand_edited_access_log_line_is_refused_rather_than_repaired(tmp_path: Path) -> None:
    """A log the reader silently fixes up proves nothing about what was read."""

    log = tmp_path / "access-log.jsonl"
    append_access_row(
        {
            "schema_version": "1.0",
            "principal": READER,
            "protocol_digest": preregistration_digest(),
            "accessed_at": AT.isoformat(),
            "reason": REASON,
        },
        path=log,
    )
    assert len(read_access_log(log)) == 1

    log.write_text(
        log.read_text(encoding="utf-8")
        + json.dumps({"principal": READER, "reason": "no digest, no timestamp"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="is not a test-set access row"):
        read_access_log(log)

    # A log that has never been written reads as no accesses rather than as an error, so a
    # fresh checkout does not need an empty file to exist.
    assert read_access_log(tmp_path / "never-written.jsonl") == ()


# --------------------------------------------------------------------------------------
# AC4-M10-046 — every registered comparator runs on the locked evaluation half.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M10-046")
def test_every_one_of_the_eleven_policies_runs_on_the_locked_evaluation_half() -> None:
    """§8.1's whole field, named one by one, on the corpus the release quotes.

    Named rather than counted: eleven rows is also what ten runnable methods and one duplicate
    produce. Each one must have selected on every evaluation task, produced a regret report and
    produced a gate report, because a policy that came back ``available`` with no rows would
    satisfy a weaker assertion while contributing nothing to the table.
    """

    locked, _drift, _log = setup_release_reads()
    expected = set(RouterBenchmarkCorpus.load(LOCKED_CORPUS_ROOT).task_ids_for(
        BenchmarkSplit.EVALUATION
    ))
    assert len(expected) == 18

    assert tuple(item.policy_id for item in locked.result.policies) == BASELINE_ORDER
    assert len(BASELINE_ORDER) == 11
    for policy_id in BASELINE_ORDER:
        policy = locked.policy(policy_id)
        assert policy.available, (policy_id, policy.reason_code)
        assert policy.reason_code is None, policy_id
        assert {row.task_id for row in policy.rows} == expected, policy_id
        assert policy.regret is not None and policy.gates is not None, policy_id
        assert policy.mean_utility is not None, policy_id
        assert policy.estimands is not None, policy_id

    # Non-vacuity: the eleven are eleven different methods, not one method under eleven names.
    choices = {
        policy_id: tuple(row.selected_candidate_id for row in locked.policy(policy_id).rows)
        for policy_id in BASELINE_ORDER
    }
    assert len(set(choices.values())) > 1
    assert choices["M0"] != choices[PRIMARY_POLICY_ID]


# --------------------------------------------------------------------------------------
# AC4-M10-050 — the three ways to announce a result that is not there.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M10-050")
def test_a_router_that_is_the_baseline_by_construction_makes_no_superiority_claim() -> None:
    """The negative control: no opportunity, no gain, no recovered fraction, no interval.

    Every configuration scores identically on every task, so the per-task oracle *is* the
    baseline and there is nothing for a router to recover. ``recovered_fraction`` must be
    ``None`` — not ``0.0``, which is a number and would read as "the router recovered none of a
    real opportunity" — and the paired interval's lower limit must not be above zero.
    """

    selection = [f"sel-{index:02d}" for index in range(12)]
    evaluation = [f"ev-{index:02d}" for index in range(24)]
    identical = {task_id: index % 2 for index, task_id in enumerate(selection + evaluation)}
    outcomes = {"cfg-a": dict(identical), "cfg-b": dict(identical)}

    measured = estimands(
        outcomes,
        {task_id: "cfg-a" for task_id in evaluation},
        {task_id: "cfg-b" for task_id in evaluation},
        selection,
        evaluation,
        k_configs=6,
    )
    assert measured.g_out == 0.0
    assert measured.g_z == 0.0
    assert measured.g_learn == 0.0
    assert measured.recovered_fraction is None
    assert measured.intervals["g_out"][0] <= 0.0

    pairs = {"prj-a": [(0.4, 0.4)] * 12, "prj-b": [(0.9, 0.9)] * 12}
    lower, upper = paired_regret_ci(pairs, 500, 20260906, measured.adjusted_alpha)
    assert lower <= 0.0 <= upper


def test_a_positive_opportunity_gap_does_report_a_recovered_fraction() -> None:
    """The companion to the negative control: the guard is a guard and not an off switch.

    Two configurations that alternate wins, and a router that always picks the winner. The
    opportunity is real, its interval's lower limit is above zero even at the adjusted level,
    and the recovered fraction is therefore a number — 1.0, because this router recovered all
    of it. Without this test, deleting the whole ``recovered_fraction`` computation would still
    leave the negative control green.
    """

    selection = [f"sel-{index:02d}" for index in range(20)]
    evaluation = [f"ev-{index:02d}" for index in range(60)]
    tasks = selection + evaluation
    outcomes = {
        "cfg-a": {task_id: int(index % 2 == 0) for index, task_id in enumerate(tasks)},
        "cfg-b": {task_id: int(index % 2 == 1) for index, task_id in enumerate(tasks)},
    }
    winner = {
        task_id: ("cfg-a" if index % 2 == 0 else "cfg-b")
        for index, task_id in enumerate(tasks)
        if task_id in set(evaluation)
    }

    measured = estimands(outcomes, winner, winner, selection, evaluation, k_configs=6)
    assert measured.g_out == pytest.approx(0.5)
    assert measured.intervals["g_out"][0] > 0.0
    assert measured.recovered_fraction == pytest.approx(1.0)


@pytest.mark.acceptance("AC4-M10-050")
def test_editing_one_frozen_field_makes_the_locked_runner_refuse_with_both_digests(
    tmp_path: Path,
) -> None:
    """An amended pre-registration is a new pre-registration, and the error names both digests.

    The edit is deliberately tiny and deliberately *semantic*: item 4's ``alpha = 0.05`` becomes
    ``alpha = 0.10``, which is the single character that would turn a null result into a
    significant one. A digest over the bytes cannot tell that edit from a whitespace fix, and it
    is not supposed to: a normalisation nobody can restate exactly is not a freeze.
    """

    frozen = PREREGISTRATION_PATH.read_text(encoding="utf-8")
    assert "`alpha = 0.05`" in frozen
    amended = tmp_path / "preregistration.md"
    amended.write_text(frozen.replace("`alpha = 0.05`", "`alpha = 0.10`", 1), encoding="utf-8")

    runner, log = setup_runner(tmp_path, preregistration_path=amended)
    with pytest.raises(PreregistrationDrift) as error:
        runner.run(["M0"], principal=READER, reason=REASON, accessed_at=AT)

    message = str(error.value)
    pinned = RouterBenchmarkCorpus.load(LOCKED_CORPUS_ROOT).config.preregistration_sha256
    assert pinned is not None
    assert pinned in message, "the error must quote the digest the corpus was frozen against"
    assert preregistration_digest(amended) in message, "and the digest found on disk"
    assert pinned != preregistration_digest(amended)
    assert read_access_log(log) == ()

    # The unedited page still hashes to the pin, so the refusal above is about the edit and not
    # about the check being permanently red.
    assert preregistration_digest() == pinned


@pytest.mark.acceptance("AC4-M10-050")
def test_a_difference_significant_at_alpha_is_not_significant_at_the_adjusted_level() -> None:
    """§13's multiplicity: eleven of twenty against zero of twenty clears α and not α/(K + L).

    The family is ``K + L`` = 6 configurations plus 3 reported policies = 9, so the adjusted
    level is α/9 and every interval — including the baseline's own — is quoted there. This is
    the comparison a benchmark reports as a finding if it corrects for the three policies it
    reported and forgets that choosing the baseline was a comparison too.
    """

    adjusted = bonferroni(0.05, 9)
    assert adjusted == pytest.approx(0.05 / 9)

    better_at_alpha = clopper_pearson(11, 20, 0.05)
    worse_at_alpha = clopper_pearson(0, 20, 0.05)
    assert better_at_alpha[0] - worse_at_alpha[1] > 0.0

    better_adjusted = clopper_pearson(11, 20, adjusted)
    worse_adjusted = clopper_pearson(0, 20, adjusted)
    assert better_adjusted[0] - worse_adjusted[1] <= 0.0

    # The adjusted interval is the wider one, which is the only reason the sign can flip.
    assert better_adjusted[0] < better_at_alpha[0]
    assert worse_adjusted[1] > worse_at_alpha[1]

    # And the level the locked run actually quotes is that same α/9 rather than α.
    locked, _drift, _log = setup_release_reads()
    measured = locked.policy(PRIMARY_POLICY_ID).estimands
    assert measured is not None
    assert measured.adjusted_alpha == pytest.approx(adjusted)


def test_the_release_flag_alone_decides_whether_the_locked_set_may_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ACCRETION_ROUTER_LOCKED_TEST`` reaches the runner through ``Settings`` and nothing else.

    Exercised through the environment rather than by injecting settings, because the claim is
    about the variable an operator exports. The runner builds its own ``Settings`` instead of
    using the process-wide cache for exactly this reason: a cache populated at import time would
    answer for the environment as it was, not as the operator set it.
    """

    monkeypatch.delenv("ACCRETION_ROUTER_LOCKED_TEST", raising=False)
    runner = LockedTestRunner(LOCKED_CORPUS_ROOT, access_log_path=tmp_path / "log.jsonl")
    with pytest.raises(LockedTestNotReleased, match="ACCRETION_ROUTER_LOCKED_TEST=1"):
        runner.run(["M0"], principal=READER, reason=REASON, accessed_at=AT)
    assert read_access_log(tmp_path / "log.jsonl") == ()

    monkeypatch.setenv("ACCRETION_ROUTER_LOCKED_TEST", "1")
    released = LockedTestRunner(LOCKED_CORPUS_ROOT, access_log_path=tmp_path / "log.jsonl")
    read = released.run(["M0"], principal=READER, reason=REASON, accessed_at=AT)
    assert read.policy("M0").available
    assert len(read_access_log(tmp_path / "log.jsonl")) == 1

    # "0", "true" and an empty value are all not-released: the flag is one value, not a
    # truthiness test a stray export can satisfy.
    monkeypatch.setenv("ACCRETION_ROUTER_LOCKED_TEST", "0")
    with pytest.raises(LockedTestNotReleased):
        LockedTestRunner(LOCKED_CORPUS_ROOT, access_log_path=tmp_path / "log.jsonl").run(
            ["M0"], principal=READER, reason=REASON, accessed_at=AT
        )


# --------------------------------------------------------------------------------------
# The page.
# --------------------------------------------------------------------------------------


def test_the_results_page_quotes_the_blocks_the_locked_run_generates() -> None:
    """Every fenced block in ``results.md`` is regenerated here and compared character by
    character.

    This is what makes the page evidence rather than prose. The comparison is on the whole
    mapping, so a block the page dropped, renamed, reordered a row inside, or hand-adjusted by
    one digit is red — and so is a page that quotes a block the run no longer produces.
    """

    locked, drift, _log = setup_release_reads()
    generated = render_blocks(locked, drift)
    assert sorted(generated) == [
        "ablations-locked",
        "corpora",
        "estimands-drift",
        "estimands-locked",
        "gates-drift",
        "gates-locked",
        "utility-locked",
    ]

    # Only the ``text`` fences: the page also carries ``bash`` fences for the reproduce
    # commands, and a comparison that swept those in would be asserting that the commands are
    # generated too. Splitting on the fence marker rather than parsing markdown keeps the
    # extraction as dumb as the thing it is checking.
    page = RESULTS_PATH.read_text(encoding="utf-8")
    fenced = [
        block.removeprefix("text\n").strip("\n")
        for index, block in enumerate(page.split("```"))
        if index % 2 == 1 and block.startswith("text\n")
    ]
    quoted = {block_key(text): text for text in fenced}
    assert len(quoted) == len(fenced), "results.md quotes one block twice"
    assert quoted == generated, (
        "docs/research/v0.4/results.md no longer quotes what the locked run produces; "
        "regenerate it with `ACCRETION_ROUTER_LOCKED_TEST=1 python scripts/router_locked_test.py`"
    )

    # The ten registered ablations are all in the page, and the drift holdout is reported
    # beside the locked result rather than instead of it.
    for ablation_id in ABLATION_IDS:
        assert f"\n{ablation_id} " in generated["ablations-locked"].replace("  ", " ")
    assert "drift" in generated["corpora"]
    assert generated["estimands-drift"] != generated["estimands-locked"]


def test_the_committed_access_log_holds_the_release_read_and_nothing_else() -> None:
    """The audited quantity: two rows, one per locked corpus, both against the frozen digest.

    Two and not one because the drift holdout is a locked corpus too and reading it is a read.
    A row whose ``protocol_digest`` is not the current pin would mean the page was produced
    under a pre-registration that has since been amended, which is the finding this file exists
    to make visible rather than to prevent.
    """

    rows = read_access_log(ACCESS_LOG_PATH)
    assert len(rows) == 2, f"{ACCESS_LOG_PATH} holds {len(rows)} rows; the release read is two"
    digest = preregistration_digest()
    for row in rows:
        assert row["protocol_digest"] == digest
        assert row["schema_version"] == "1.0"
        assert datetime.fromisoformat(row["accessed_at"]).tzinfo is not None
        assert row["principal"] and row["reason"]
    assert sorted(row["reason"] for row in rows) != [rows[0]["reason"], rows[0]["reason"]]
