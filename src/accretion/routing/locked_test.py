"""The locked test set: three refusals, one committed log, and the tables ``results.md`` quotes.

Every other corpus in this repository is a corpus somebody iterated on. ``evals/router`` was
read while M2 through M10c were written, which is exactly what a development corpus is for and
exactly why a number measured on it is not evidence: a method tuned until the development half
looked right has been fitted to that half, whatever the code says. So the numbers the v0.4
release quotes come from two corpora nobody has iterated on — ``evals/router/locked`` and the
provider-drift holdout ``evals/router/drift`` — and this module is the door they are read
through.

**Three refusals, in order.**

1. *The pre-registration must still be the one that was frozen.* The corpus's
   ``preregistration_sha256`` is compared with the sha256 of
   ``docs/research/v0.4/preregistration.md`` on disk, and both digests go in the error. A
   benchmark whose registered analysis can be edited after the rows are seen has one free
   parameter per surprising result, and an amended pre-registration is a *new*
   pre-registration with a new pin, never a quiet edit.
2. *The read must be released deliberately.* ``ACCRETION_ROUTER_LOCKED_TEST=1``, read through
   :class:`~accretion.config.Settings` as ``router_locked_test``. Not because an environment
   variable is hard to set, but because setting it is a thing a person does on purpose and can
   be found in a shell history, where importing a module is not.
3. *Nothing that was fitted may have seen these projects.* Every
   :class:`~accretion.contracts.routing.RouterTrainingSnapshot` handed to the runner is checked
   against the corpus's own project ids, and any mention at all — training, validation *or*
   holdout — raises :class:`~accretion.routing.split.SplitViolation`. Holdout is refused along
   with the other two on purpose: these corpora are a separate world from the development
   registry, and a snapshot that names one of their projects is a snapshot built from a
   registry somebody merged, which is a leak whichever group it landed in.

**The log is a file, not a row.** :class:`~accretion.routing.split.TestSetAccessLog` is
in-memory, and the obvious place to persist it — a ``BenchmarkRun`` row — cannot hold it:
``BenchmarkRun.suite`` is ``Literal["ACR-ARCH"]`` and the contracts are frozen. So each
successful read appends one ``TestSetAccessEntry.to_rows()`` row to
``docs/research/v0.4/access-log.jsonl``, a committed append-only JSONL (ADR4-M10-003). A
committed file is a weaker guarantee than an append-only table and a much stronger one than a
process-local list: over-reading the locked set shows up in a diff that a reviewer has to
approve, which is the only enforcement a repository can actually offer.

**The development corpus never comes through here.** :meth:`LockedTestRunner.run` refuses a
corpus root that is not one of the two locked directories, so nothing about iterating on
``evals/router`` writes an access-log row, and the log's row count means what the release audit
says it means.

**Rendering lives here too.** :func:`render_blocks` turns a read into the exact text blocks
``docs/research/v0.4/results.md`` carries, so the page is *generated and quoted* rather than
typed: a test regenerates the blocks and diffs them against the page's fenced code fences, and
a number nobody can reproduce cannot survive in the page. The blocks keep the two safety gates
in a different table from utility, for the reason :class:`~accretion.router_benchmark.\
GateReport` exists: a single figure would let a method that bought mean utility with a false
acceptance move both columns and cancel.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from accretion.config import Settings
from accretion.contracts.routing import RouterTrainingSnapshot
from accretion.router_benchmark import (
    CORPUS_ROOT,
    REPOSITORY_ROOT,
    BenchmarkSplit,
    PolicyResult,
    RouterBenchmarkCorpus,
    RouterBenchmarkResult,
    RouterBenchmarkRunner,
)
from accretion.routing.flags import FULL, RouterFeatureFlags
from accretion.routing.split import SplitViolation, TestSetAccessEntry, TestSetAccessLog
from accretion.routing.stats import Interval, paired_regret_ci

LOCKED_CORPUS_ROOT = CORPUS_ROOT / "locked"
DRIFT_CORPUS_ROOT = CORPUS_ROOT / "drift"
"""The two corpora this module guards. Directories, because a corpus is four documents and a
registry; see ADR4-M10-004 and :data:`~tests.router_corpus_generator.LOCKED_ROOT`."""

PREREGISTRATION_PATH = REPOSITORY_ROOT / "docs" / "research" / "v0.4" / "preregistration.md"
"""The frozen §21 page whose sha256 every locked corpus pins."""

ACCESS_LOG_PATH = REPOSITORY_ROOT / "docs" / "research" / "v0.4" / "access-log.jsonl"
AMENDMENT_1_PATH = REPOSITORY_ROOT / "docs" / "research" / "v0.4" / "amendment-1.md"
"""The first protocol amendment. A corpus that pins ``amendment_1_sha256`` runs under it, and the
runner checks that pin the way it checks the pre-registration's: both documents, before any read."""
"""The committed append-only log. One JSON object per line, in the order reads happened."""

ACCESS_LOG_FIELDS: tuple[str, ...] = (
    "schema_version",
    "principal",
    "protocol_digest",
    "accessed_at",
    "reason",
)
"""Exactly the keys :meth:`~accretion.routing.split.TestSetAccessLog.to_rows` emits.

Restated so that :func:`read_access_log` refuses a hand-edited line that dropped or added a
field. A log whose rows a reader has to guess the shape of is a log nobody audits."""

ABLATION_IDS: tuple[str, ...] = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10")
"""Protocol §14's ten registered ids, in report order.

Written out rather than read from the registry for the reason
``tests/test_v04_m10_ablations.py`` writes them out: a report that took its own row list from
the table under report would silently shrink to nine if an entry were deleted."""

PRIMARY_POLICY_ID = "M9"
"""Pre-registration item 2's one primary comparison: the full v0.4 guarded router."""

FIXED_BASELINE_POLICY_ID = "M0"
"""§8.1's strongest-fixed comparator, the arm the paired regret contrast is taken against.

The *same* configuration :attr:`~accretion.routing.stats.Estimands.best_fixed` names:
:class:`~accretion.routing.baselines.StrongestFixedPolicy` delegates its argmax to
:func:`~accretion.routing.stats.select_best_fixed`, so M0 is pre-registration item 2's baseline
applied to every task. That is what makes the paired regret interval and ``g_learn`` two
readings of one comparison rather than two comparisons — utility and the binary endpoint — and
it is why the page reports both: they can disagree, and when they do the disagreement is the
result."""

_BOOTSTRAP_REPLICATES = 2_000
"""Pre-registration item 5's B. The same value :mod:`accretion.router_benchmark` uses, so the
paired interval on this page and the per-policy interval in a run are the same estimator."""


class LockedTestRefused(RuntimeError):
    """The locked test set was asked for and this module said no.

    One base class over both pre-run refusals so a caller can catch "the locked read did not
    happen" without enumerating the reasons, while the two subclasses keep the reasons apart
    for the caller that has to act differently — a stale digest is a protocol amendment to
    write, a missing flag is a command to rerun.
    """


class PreregistrationDrift(LockedTestRefused):
    """The pre-registration on disk no longer hashes to the digest the corpus pinned."""


class LockedTestNotReleased(LockedTestRefused):
    """``ACCRETION_ROUTER_LOCKED_TEST`` is not ``1``, so nothing may read the locked set."""


def preregistration_digest(path: Path = PREREGISTRATION_PATH) -> str:
    """The sha256 of the pre-registration file, over its bytes and not its rendered text.

    Bytes, so that a trailing-whitespace fix is a protocol amendment too. That is deliberately
    strict: the alternative is a normalisation nobody can restate exactly, and a digest whose
    definition is negotiable is not a freeze.
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def amendment_1_digest(path: Path = AMENDMENT_1_PATH) -> str:
    """sha256 of the amendment page's bytes, computed the way the pre-registration's is."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_no_training_overlap(
    snapshots: Iterable[RouterTrainingSnapshot], *, project_ids: Iterable[str]
) -> None:
    """Raise :class:`SplitViolation` if any snapshot names a locked or drift project.

    All three snapshot groups are checked, not only ``training``. A router fitted on a snapshot
    that *sealed* a locked project into its holdout was still built from a registry that
    contains it, and the next snapshot from that registry is one seed away from putting it in
    training. Refusing the whole mention is the check that keeps working after somebody
    "fixes" the split.

    Every offending pair is reported together rather than the first one found: a caller that
    has to rerun to discover the second leak will conclude the first fix worked.
    """

    locked = set(project_ids)
    failures: list[str] = []
    for index, snapshot in enumerate(snapshots):
        groups = {
            "training": snapshot.split.training_project_ids,
            "validation": snapshot.split.validation_project_ids,
            "holdout": snapshot.split.holdout_project_ids,
        }
        for group, ids in sorted(groups.items()):
            shared = sorted(locked.intersection(ids))
            if shared:
                failures.append(f"snapshot[{index}].split.{group} names {shared!r}")
    if failures:
        raise SplitViolation(
            "a training snapshot names projects from the locked test corpus, so a method "
            "fitted under it has already seen the set it is about to be scored on: "
            + "; ".join(failures)
        )


def read_access_log(path: Path = ACCESS_LOG_PATH) -> tuple[dict[str, str], ...]:
    """Every recorded read, in the order it was appended.

    A missing file reads as no accesses, which is the honest reading of "the locked test set
    has never been read" and keeps a fresh checkout from needing an empty file to exist. A
    malformed or short line raises: a log the reader silently repairs proves nothing.
    """

    if not path.is_file():
        return ()
    rows: list[dict[str, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(ACCESS_LOG_FIELDS)):
            raise ValueError(
                f"{path}:{number} is not a test-set access row; expected exactly the keys "
                f"{sorted(ACCESS_LOG_FIELDS)!r}"
            )
        rows.append({key: str(row[key]) for key in ACCESS_LOG_FIELDS})
    return tuple(rows)


def append_access_row(row: Mapping[str, str], *, path: Path = ACCESS_LOG_PATH) -> None:
    """Append one row, sorted-key and newline-terminated, creating the file if it is absent.

    Opened in append mode and never rewritten, so a bug in this module cannot lose a row that
    is already there; deleting one takes an edit that shows up in a diff.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


@dataclass(frozen=True, slots=True)
class AblationRun:
    """One protocol §14 ablation's result on the locked corpus, and which id produced it."""

    ablation_id: str
    removed: tuple[str, ...]
    result: RouterBenchmarkResult


@dataclass(frozen=True, slots=True)
class LockedTestRead:
    """Everything one released read of a locked corpus produced.

    The access entry is carried beside the numbers rather than only written to the log,
    because the thing that makes a locked result quotable is *which* read produced it: a report
    holding a result and no entry could have come from any read, including one nobody recorded.
    """

    corpus_root: Path
    corpus: RouterBenchmarkCorpus
    entry: TestSetAccessEntry
    result: RouterBenchmarkResult
    ablations: tuple[AblationRun, ...]

    def policy(self, policy_id: str) -> PolicyResult:
        """One comparator's result on this read."""

        return self.result.policy(policy_id)

    def paired_regret_interval(
        self,
        candidate_policy_id: str = PRIMARY_POLICY_ID,
        baseline_policy_id: str = FIXED_BASELINE_POLICY_ID,
    ) -> Interval:
        """Item 5's primary analysis: the clustered interval on ``baseline − candidate`` regret.

        Paired on the task and clustered on the project, in that order, which is what removes
        the per-node difficulty both arms share before the between-project variance is put
        back. Positive means the candidate regretted less, so "an interval excluding no
        improvement" is a lower limit above zero and nothing else.
        """

        baseline = self.result.policy(baseline_policy_id).regret
        candidate = self.result.policy(candidate_policy_id).regret
        if baseline is None or candidate is None:
            raise ValueError(
                f"both {baseline_policy_id} and {candidate_policy_id} must have run to be "
                "paired; one of them reported no regret at all"
            )
        by_task = {row.task_id: row for row in candidate.rows}
        pairs: dict[str, list[tuple[float, float]]] = {}
        for row in baseline.rows:
            other = by_task.get(row.task_id)
            if other is None:
                raise ValueError(
                    f"{candidate_policy_id} has no row for task {row.task_id!r}; a paired "
                    "contrast over two different task sets is not a paired contrast"
                )
            pairs.setdefault(row.project_id, []).append((row.regret, other.regret))
        return paired_regret_ci(
            pairs, _BOOTSTRAP_REPLICATES, self.corpus.config.seed, self.result_alpha
        )

    @property
    def result_alpha(self) -> float:
        """The multiplicity-adjusted level every interval on this read is quoted at.

        Taken from the estimands the run computed rather than recomputed, so the paired
        interval and the three rate intervals cannot end up at two different levels — which is
        the way a "corrected" analysis usually leaks its alpha.
        """

        estimands = self.result.policy(PRIMARY_POLICY_ID).estimands
        if estimands is None:
            raise ValueError(
                f"{PRIMARY_POLICY_ID} reported no estimands, so there is no adjusted level to "
                "quote the paired interval at"
            )
        return estimands.adjusted_alpha


class LockedTestRunner:
    """The one door onto ``evals/router/locked`` and ``evals/router/drift``.

    Constructed with the corpus root and, for tests, the two paths and the settings it reads.
    The paths are parameters rather than module lookups so that a test can prove the append
    behaviour against a log in ``tmp_path`` without writing a row into the release's committed
    log — the row count in that file is audited, and a suite that added to it on every run
    would make the audit meaningless.
    """

    def __init__(
        self,
        corpus_root: Path = LOCKED_CORPUS_ROOT,
        *,
        preregistration_path: Path = PREREGISTRATION_PATH,
        access_log_path: Path = ACCESS_LOG_PATH,
        amendment_path: Path = AMENDMENT_1_PATH,
        settings: Settings | None = None,
    ) -> None:
        allowed = (LOCKED_CORPUS_ROOT.resolve(), DRIFT_CORPUS_ROOT.resolve())
        if corpus_root.resolve() not in allowed:
            raise LockedTestRefused(
                f"{corpus_root} is not a locked corpus; this runner reads only "
                f"{LOCKED_CORPUS_ROOT} and {DRIFT_CORPUS_ROOT}, and a development corpus that "
                "could be read through it would put untrustworthy rows in the access log"
            )
        self.corpus_root = corpus_root
        self.preregistration_path = preregistration_path
        self.access_log_path = access_log_path
        self.amendment_path = amendment_path
        # A freshly constructed Settings, not the process-wide ``get_settings()`` cache. The
        # release flag has to describe *this* run: a cache populated by an import that happened
        # before the operator exported the variable would release or refuse the read for a
        # reason that has nothing to do with what the operator asked for.
        self.settings = settings if settings is not None else Settings()
        self.log = TestSetAccessLog()

    def run(
        self,
        policy_ids: Sequence[str],
        *,
        principal: str,
        reason: str,
        accessed_at: datetime,
        snapshots: Iterable[RouterTrainingSnapshot] = (),
        ablation_ids: Sequence[str] = (),
        ablation_policy_ids: Sequence[str] = (PRIMARY_POLICY_ID,),
        flags: RouterFeatureFlags = FULL,
    ) -> LockedTestRead:
        """Check the three refusals, record the access, then replay the evaluation half.

        The order is not incidental. The digest and the release flag are checked before the
        corpus is even loaded, so a refused attempt reads nothing; the snapshot check runs
        after the corpus is loaded because it needs the corpus's project ids; and the access is
        recorded **before** the run, so an access that crashed halfway through is still an
        access that happened. A log written on success only would be a log that under-counts
        exactly the reads somebody had a reason not to finish.
        """

        corpus = RouterBenchmarkCorpus.load(self.corpus_root)
        pinned = corpus.config.preregistration_sha256
        on_disk = preregistration_digest(self.preregistration_path)
        if pinned is None:
            raise PreregistrationDrift(
                f"the corpus at {self.corpus_root} pins no preregistration_sha256, so its "
                "registered analysis is not frozen and nothing may be measured on it"
            )
        if pinned != on_disk:
            raise PreregistrationDrift(
                f"{self.preregistration_path} hashes to {on_disk}, but the corpus at "
                f"{self.corpus_root} was frozen against {pinned}; an amended pre-registration "
                "is a new pre-registration with a new pin, not an edit"
            )
        amended = corpus.config.amendment_1_sha256
        if amended is not None:
            amendment_on_disk = amendment_1_digest(self.amendment_path)
            if amended != amendment_on_disk:
                raise PreregistrationDrift(
                    f"{self.amendment_path} hashes to {amendment_on_disk}, but the corpus at "
                    f"{self.corpus_root} was frozen against amendment digest {amended}; an "
                    "amended protocol is two documents and both must still be the ones pinned"
                )
        if not self.settings.router_locked_test:
            raise LockedTestNotReleased(
                "reading the locked test set requires ACCRETION_ROUTER_LOCKED_TEST=1 "
                f"(Settings.router_locked_test is {self.settings.router_locked_test!r}); the "
                "flag exists so that a read is something an operator did on purpose"
            )
        project_ids = {task.project_id for task in corpus.tasks}
        assert_no_training_overlap(snapshots, project_ids=project_ids)

        entry = self.log.record(
            principal=principal,
            protocol_digest=pinned,
            accessed_at=accessed_at,
            reason=reason,
        )
        append_access_row(self.log.to_rows()[-1], path=self.access_log_path)

        runner = RouterBenchmarkRunner(corpus)
        result = runner.run(policy_ids, split=BenchmarkSplit.EVALUATION, flags=flags)
        ablations = tuple(
            AblationRun(
                ablation_id=ablation_id,
                removed=runner.ablation(ablation_id).removed,
                result=runner.run(
                    ablation_policy_ids,
                    split=BenchmarkSplit.EVALUATION,
                    flags=runner.ablation(ablation_id),
                ),
            )
            for ablation_id in ablation_ids
        )
        return LockedTestRead(
            corpus_root=self.corpus_root,
            corpus=corpus,
            entry=entry,
            result=result,
            ablations=ablations,
        )


# --------------------------------------------------------------------------------------
# Rendering: the exact blocks ``results.md`` carries.
# --------------------------------------------------------------------------------------


def _number(value: float) -> str:
    """Six decimal places, always signed the same way, so two runs diff cleanly."""

    return f"{value:.6f}"


def _interval(bounds: Interval) -> str:
    return f"[{_number(bounds[0])}, {_number(bounds[1])}]"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """A fixed-width text table. Widths come from the content, so the text is a function of it."""

    widths = [
        max(len(str(header[column])), *(len(str(row[column])) for row in rows))
        if rows
        else len(str(header[column]))
        for column in range(len(header))
    ]
    lines = ["  ".join(str(header[i]).ljust(widths[i]) for i in range(len(header))).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(header))))
    for row in rows:
        lines.append("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(header))).rstrip())
    return lines


def block_key(block: str) -> str:
    """The name a rendered block is filed under: the first word of its ``# `` heading line.

    The key is carried *in* the block rather than only in the mapping, so a fenced block copied
    into ``results.md`` still says which generated block it is. A marker in an HTML comment
    would have been invisible and would have survived being pasted under the wrong table.
    """

    heading = block.splitlines()[0]
    if not heading.startswith("# "):
        raise ValueError(f"a rendered block must open with '# <key>'; got {heading!r}")
    return heading.removeprefix("# ").split()[0]


def _mean_regret(policy: PolicyResult) -> float:
    if policy.regret is None:
        raise ValueError(f"policy {policy.policy_id} produced no regret report to quote")
    return policy.regret.mean_regret


def _corpora_block(reads: Sequence[LockedTestRead]) -> str:
    rows = [
        [
            read.corpus_root.name,
            str(read.corpus.config.seed),
            str(len(read.corpus.tasks)),
            str(len(read.corpus.candidates)),
            str(len(read.corpus.traces) // (len(read.corpus.tasks) * len(read.corpus.candidates))),
            read.corpus.corpus_sha256[:12],
            read.corpus.trace_sha256[:12],
            read.result.run_id,
        ]
        for read in reads
    ]
    return "\n".join(
        [
            "# corpora - the two corpora nobody iterated on",
            *_table(
                ("corpus", "seed", "tasks", "configs", "trials", "digest", "traces", "run"), rows
            ),
        ]
    )


def _estimands_block(read: LockedTestRead, *, key: str, name: str) -> str:
    estimands = read.policy(PRIMARY_POLICY_ID).estimands
    if estimands is None:
        raise ValueError(f"{PRIMARY_POLICY_ID} reported no estimands on the {name} corpus")
    best = estimands.best_fixed
    reduction = _mean_regret(read.policy(FIXED_BASELINE_POLICY_ID)) - _mean_regret(
        read.policy(PRIMARY_POLICY_ID)
    )
    rows = [
        [
            "g_out (oracle opportunity)",
            _number(estimands.g_out),
            _interval(estimands.intervals["g_out"]),
        ],
        [
            "g_z (signal-restricted opportunity)",
            _number(estimands.g_z),
            _interval(estimands.intervals["g_z"]),
        ],
        [
            "g_learn (learned gain)",
            _number(estimands.g_learn),
            _interval(estimands.intervals["g_learn"]),
        ],
        [
            f"best fixed configuration ({best.config_id})",
            f"{best.evaluation_successes}/{best.evaluation_trials}",
            _interval(best.evaluation_interval),
        ],
        [
            f"paired regret reduction ({FIXED_BASELINE_POLICY_ID} - {PRIMARY_POLICY_ID})",
            _number(reduction),
            _interval(read.paired_regret_interval()),
        ],
    ]
    recovered = (
        "not reported - the opportunity gap's lower limit is not positive"
        if estimands.recovered_fraction is None
        else _number(estimands.recovered_fraction)
    )
    return "\n".join(
        [
            f"# {key} - {name} corpus, EVALUATION half, {PRIMARY_POLICY_ID} against the "
            "selection-valid best fixed configuration",
            *_table(("estimand", "value", "interval at the adjusted level"), rows),
            "",
            f"selection-half rate  {best.selection_successes}/{best.selection_trials}",
            f"adjusted alpha       {_number(estimands.adjusted_alpha)} "
            "(Bonferroni over K = 6 configurations and L = 3 policies)",
            f"recovered fraction   {recovered}",
        ]
    )


def _gates_block(read: LockedTestRead, *, key: str, name: str) -> str:
    rows: list[list[str]] = []
    floor = "-"
    ceiling = "-"
    for policy in read.result.policies:
        gates = policy.gates
        if gates is None:
            rows.append([policy.policy_id, "-", "-", "-", "-", "-", str(policy.reason_code)])
            continue
        floor = _number(gates.verified_success_floor)
        ceiling = _number(gates.false_acceptance_ceiling)
        rows.append(
            [
                policy.policy_id,
                str(gates.selections),
                f"{gates.verified_successes} ({_number(gates.verified_success_rate)})",
                "MET" if gates.verified_success_met else "BELOW FLOOR",
                f"{gates.false_acceptances} ({_number(gates.false_acceptance_rate)})",
                "MET" if gates.false_acceptance_met else "OVER CEILING",
                "PASS" if gates.both_met else "FAIL",
            ]
        )
    return "\n".join(
        [
            f"# {key} - {name} corpus, EVALUATION half, verified-success floor {floor}, "
            f"false-acceptance ceiling {ceiling}",
            *_table(
                ("policy", "n", "verified", "floor", "false accepts", "ceiling", "both"), rows
            ),
        ]
    )


def _utility_block(read: LockedTestRead, *, key: str, name: str) -> str:
    rows: list[list[str]] = []
    for policy in read.result.policies:
        if policy.regret is None or policy.mean_utility is None:
            rows.append([policy.policy_id, "-", "-", "-", str(policy.reason_code)])
            continue
        rows.append(
            [
                policy.policy_id,
                _number(policy.mean_utility),
                _number(policy.regret.mean_regret),
                _interval(policy.regret_interval) if policy.regret_interval else "-",
                str(policy.regret.safety.invalid_selections),
            ]
        )
    return "\n".join(
        [
            f"# {key} - {name} corpus, EVALUATION half, reported apart from the gates",
            *_table(
                ("policy", "mean utility", "mean regret", "regret interval", "invalid"), rows
            ),
        ]
    )


def _ablations_block(read: LockedTestRead, *, key: str, name: str) -> str:
    full = read.policy(PRIMARY_POLICY_ID)
    if full.mean_utility is None:
        raise ValueError(f"{PRIMARY_POLICY_ID} produced no utility to ablate against")
    rows: list[list[str]] = []
    for ablation in read.ablations:
        policy = ablation.result.policy(PRIMARY_POLICY_ID)
        gates = policy.gates
        if policy.regret is None or policy.mean_utility is None or gates is None:
            rows.append([ablation.ablation_id, ", ".join(ablation.removed), "-", "-", "-", "-"])
            continue
        rows.append(
            [
                ablation.ablation_id,
                ", ".join(ablation.removed),
                _number(policy.mean_utility),
                _number(policy.mean_utility - full.mean_utility),
                _number(policy.regret.mean_regret),
                f"{gates.verified_successes}/{gates.selections}",
            ]
        )
    return "\n".join(
        [
            f"# {key} - {name} corpus, EVALUATION half, {PRIMARY_POLICY_ID} under each "
            f"registered §14 ablation; unablated mean utility {_number(full.mean_utility)}",
            *_table(("id", "removed", "mean utility", "delta", "mean regret", "verified"), rows),
        ]
    )


def render_blocks(locked: LockedTestRead, drift: LockedTestRead) -> dict[str, str]:
    """The named text blocks ``results.md`` quotes, keyed by the name in their heading line.

    A mapping and not one string because the page interleaves prose with the blocks, and a
    single blob would force the page to be either all generated or all typed. The keys are the
    contract between this function and the page: a regeneration test reads every fenced block
    out of ``results.md``, keys them by :func:`block_key`, and requires the two mappings to be
    equal — so a block the page dropped, renamed or hand-edited is as red as a number that
    moved.

    The drift holdout is rendered with the same two functions as the locked corpus and never
    with a summarising variant, because "reported beside it" has to mean the same estimator on
    the same scale; a holdout summarised more coarsely than the headline is a holdout nobody
    can check the headline against.
    """

    blocks = [
        _corpora_block((locked, drift)),
        _estimands_block(locked, key="estimands-locked", name="locked"),
        _gates_block(locked, key="gates-locked", name="locked"),
        _utility_block(locked, key="utility-locked", name="locked"),
        _ablations_block(locked, key="ablations-locked", name="locked"),
        _estimands_block(drift, key="estimands-drift", name="drift"),
        _gates_block(drift, key="gates-drift", name="drift"),
    ]
    return {block_key(block): block for block in blocks}
