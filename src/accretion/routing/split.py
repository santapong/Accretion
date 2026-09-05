"""Project-lineage splitting for the router benchmark: one lineage, one split.

Protocol §203-226 makes the grouping key of every router evaluation the **project lineage**
and not the project. A fork, a derived benchmark, a paper extension, a re-tagged repository
version and a generated node instance are all the same evidence wearing different
``project_id``s, and a holdout that contains one of them while training contains another
measures memorisation and reports it as generalisation. The five splits the protocol
requires — training, calibration, development, a locked test set and (promoted to required
by the program plan) a temporal/provider-drift holdout — are therefore allocated over
lineage roots, never over projects.

**How a lineage is found.** :func:`lineage_roots` runs union-find over three kinds of edge,
each of which has caught a real leak somewhere:

* ``repository_identity`` — the same repository under two project ids. A fork that dropped
  its ancestry metadata still carries the upstream digest
  (:class:`accretion.experience.models.Experience` uses the same 64-hex shape), so this edge
  catches the fork nobody declared.
* ``task_family`` — two repositories that generate the same family of tasks. Generated node
  instances share nothing textually and everything structurally.
* declared ``ancestors`` — the paper-extension chain, where each link has its own repository
  and its own family and only the declaration ties them together.

An ancestor that names a project outside the registry is *still* an edge: two projects
descended from one upstream repository the corpus does not itself contain are one lineage,
and dropping unknown ancestors would silently split them.

**Why the assignment hashes the root.** :func:`assign` orders lineage roots by
``sha256(seed:root_id)`` and then fills split-sized quotas from that order. Two properties
follow. A lineage is indivisible — every project reaches its split through its root, so no
membership rule can put two members on opposite sides. And the split is reproducible from
``(registry, fractions, seed)`` alone, with no dependence on iteration order, insertion
order or the wall clock; nothing in this module reads a clock, and the one timestamp that
exists (:class:`TestSetAccessEntry`) is injected by the caller.

**What this module does not do.** It does not persist anything and it does not enforce
anything at read time. :func:`assert_disjoint` is the pure enforcer a caller runs before it
trusts a split, :meth:`SplitAssignment.to_sealed` narrows the five working splits onto the
sealed three-group :class:`~accretion.contracts.routing.SnapshotSplit` so a training
snapshot can pin them, and :class:`TestSetAccessLog` accumulates the access record in
memory with :meth:`TestSetAccessLog.to_rows` ready for a later store binding. Read-time
enforcement over persisted manifests (AC4-M10-045) belongs to a later milestone.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.routing import SnapshotSplit

PROJECTS_PATH = Path(__file__).resolve().parents[3] / "evals" / "router" / "projects.v1.json"
"""The shipped development-project registry for the router benchmark."""

_TOKEN = re.compile(r"[a-z0-9]+")


class SplitViolation(RuntimeError):
    """A split that would leak: a repeated project, or one lineage across two splits.

    Raised rather than returned. A caller that had to remember to inspect a report would
    eventually forget, and the failure mode of forgetting is a benchmark number that is too
    good and cannot be told apart from a real improvement.
    """


class SplitName(StrEnum):
    """The five required splits, in the order quotas are filled.

    ``CALIBRATION`` and ``DEVELOPMENT`` are separate because they answer different
    questions: calibration fits thresholds and probability calibrators, development is read
    repeatedly while iterating. Folding them together would let a threshold be tuned on the
    set used to decide the shape of the model. ``DRIFT`` is the temporal/provider-drift
    holdout the program plan promotes to required — a locked test set drawn from the same
    period as training cannot show provider drift, because it does not contain any.
    """

    TRAIN = "TRAIN"
    CALIBRATION = "CALIBRATION"
    DEVELOPMENT = "DEVELOPMENT"
    TEST = "TEST"
    DRIFT = "DRIFT"


SPLIT_ORDER: tuple[SplitName, ...] = (
    SplitName.TRAIN,
    SplitName.CALIBRATION,
    SplitName.DEVELOPMENT,
    SplitName.TEST,
    SplitName.DRIFT,
)
"""Canonical iteration order. Every tie in this module is broken by position in this tuple,
so that an assignment depends on the seed and never on dictionary ordering."""


class ProjectLineage(StrictModel):
    """One development project and everything known about where it came from.

    ``repository_identity`` carries the same 64-hex shape as
    :class:`accretion.experience.models.Experience`, so a registry entry and an experience
    row can be compared without a translation step. ``ancestors`` holds project ids, which
    may name projects outside this registry; see the module docstring for why they are kept.
    """

    project_id: str = Field(min_length=1, max_length=128)
    repository_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_family: str = Field(min_length=1, max_length=160)
    ancestors: list[str] = Field(default_factory=list, max_length=64)
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ancestry_is_well_formed(self) -> Self:
        if self.project_id in self.ancestors:
            raise ValueError(
                f"project {self.project_id!r} lists itself as an ancestor; a self-edge hides "
                "a copy-paste error behind a lineage that looks declared"
            )
        if len(set(self.ancestors)) != len(self.ancestors):
            raise ValueError(f"project {self.project_id!r} repeats an ancestor")
        return self


class ProjectRegistry(StrictModel):
    """The parsed ``projects.v1.json`` document.

    ``suite_version`` is a :class:`~typing.Literal` rather than free text: a corpus whose
    shape changed under a version string the reader still accepts is a corpus that produces
    numbers nobody can reproduce.
    """

    suite_version: Literal["v1"] = "v1"
    projects: list[ProjectLineage] = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def _project_ids_are_unique(self) -> Self:
        seen = [project.project_id for project in self.projects]
        duplicates = sorted({name for name in seen if seen.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"project ids {duplicates!r} appear more than once; a repeated id would "
                "weight one lineage twice and could land it in two splits"
            )
        return self


class SplitFractions(StrictModel):
    """The share of lineage roots each split receives. All five are required.

    Every fraction is ``> 0``: the protocol makes all five splits required, and a fraction
    of zero would silently retire one of them while still validating. The sum is required to
    be one within ``1e-9``, which is float equality stated honestly rather than pretended.
    """

    train: float = Field(gt=0, lt=1)
    calibration: float = Field(gt=0, lt=1)
    development: float = Field(gt=0, lt=1)
    test: float = Field(gt=0, lt=1)
    drift: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def _fractions_sum_to_one(self) -> Self:
        total = self.train + self.calibration + self.development + self.test + self.drift
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(
                f"split fractions sum to {total!r}, not 1.0; a registry split by fractions "
                "that do not sum to one either drops lineages or double-counts them"
            )
        return self

    def share_of(self, split: SplitName) -> float:
        """The fraction belonging to ``split``, by name rather than by attribute."""

        shares = {
            SplitName.TRAIN: self.train,
            SplitName.CALIBRATION: self.calibration,
            SplitName.DEVELOPMENT: self.development,
            SplitName.TEST: self.test,
            SplitName.DRIFT: self.drift,
        }
        return shares[split]


DEFAULT_FRACTIONS = SplitFractions(
    train=0.5, calibration=0.15, development=0.15, test=0.1, drift=0.1
)
"""Half the lineages for fitting, and the other half spread across the four sets that are
allowed to say no. The two holdouts are deliberately equal in size: a drift holdout smaller
than the locked test set would make provider drift the least-powered question asked."""


class SplitAssignment(StrictModel):
    """Which projects landed in which split, and the lineage map that justifies it.

    Deliberately **not** self-validating. :func:`assert_disjoint` is the enforcer, and a
    model that refused to hold a leaking assignment would make the enforcer untestable and
    would move the check to a place callers cannot run against a split they built by hand.

    ``root_by_project`` is carried rather than recomputed because the assignment is only
    meaningful against the lineage map it was built from: re-deriving roots from a registry
    that has since gained an edge would silently re-answer the question.
    """

    schema_version: Literal["1.0"] = "1.0"
    seed: int
    fractions: SplitFractions
    root_by_project: dict[str, str]
    train_project_ids: list[str] = Field(default_factory=list)
    calibration_project_ids: list[str] = Field(default_factory=list)
    development_project_ids: list[str] = Field(default_factory=list)
    test_project_ids: list[str] = Field(default_factory=list)
    drift_project_ids: list[str] = Field(default_factory=list)

    def by_split(self) -> dict[SplitName, tuple[str, ...]]:
        """Every split, in :data:`SPLIT_ORDER`, as an immutable tuple of project ids."""

        return {
            SplitName.TRAIN: tuple(self.train_project_ids),
            SplitName.CALIBRATION: tuple(self.calibration_project_ids),
            SplitName.DEVELOPMENT: tuple(self.development_project_ids),
            SplitName.TEST: tuple(self.test_project_ids),
            SplitName.DRIFT: tuple(self.drift_project_ids),
        }

    def project_ids_for(self, split: SplitName) -> tuple[str, ...]:
        """The project ids in one split."""

        return self.by_split()[split]

    def split_of(self, project_id: str) -> SplitName | None:
        """The split holding ``project_id``, or ``None``.

        Returns the *first* split in :data:`SPLIT_ORDER` that holds the project. A project
        in two splits is a leak, not an ambiguity to be resolved here, and
        :func:`assert_disjoint` is what says so.
        """

        for split, project_ids in self.by_split().items():
            if project_id in project_ids:
                return split
        return None

    def to_sealed(self) -> SnapshotSplit:
        """Narrow the five working splits onto the sealed three-group snapshot model.

        SDD §10.1 seals *training, validation, holdout* — three groups — while the protocol
        works in five. The mapping is the only one that preserves what each group is for:
        training stays training; calibration and development are the two sets a fitting
        procedure is allowed to read repeatedly, so they become ``validation``; the locked
        test set and the drift holdout are the two nobody may read before promotion, so they
        become ``holdout``. Collapsing in the other direction — putting development in the
        holdout, say — would seal a group that has already been read.

        The information the snapshot cannot carry is not lost: it stays on this object,
        which is what a caller pins beside the snapshot.
        """

        training = sorted(self.train_project_ids)
        validation = sorted([*self.calibration_project_ids, *self.development_project_ids])
        holdout = sorted([*self.test_project_ids, *self.drift_project_ids])
        if not training or not holdout:
            raise SplitViolation(
                "cannot seal a split with an empty training or holdout group "
                f"(training={len(training)}, holdout={len(holdout)}); the registry has too "
                "few lineage roots to fill the five required splits"
            )
        return SnapshotSplit(
            training_project_ids=training,
            validation_project_ids=validation,
            holdout_project_ids=holdout,
        )


class NearDuplicatePair(StrictModel):
    """Two objectives similar enough to be treated as one piece of evidence."""

    left_id: str = Field(min_length=1)
    right_id: str = Field(min_length=1)
    similarity: float = Field(ge=0, le=1)


class TestSetAccessEntry(StrictModel):
    """One recorded read of the locked test set.

    ``accessed_at`` is supplied by the caller and never read from the clock here, so a
    replayed access log reproduces byte for byte.
    """

    schema_version: Literal["1.0"] = "1.0"
    principal: str = Field(min_length=1, max_length=255)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    accessed_at: datetime
    reason: str = Field(min_length=1, max_length=1_000)


class TestSetAccessLog:
    """An append-only record of who read the locked test set, when, and why.

    Append-only is enforced by shape rather than by convention: there is no removal or
    rewrite method, and :attr:`entries` hands out a tuple, so a caller cannot edit the log
    through the value it was given. That matters because the log's only purpose is to make
    over-reading of the test set visible afterwards, and a log a reader can trim proves
    nothing.
    """

    # The protocol's name for this thing starts with "Test", so pytest would try to collect
    # it as a test class in any module that imports it. This is the documented opt-out; the
    # alternative was renaming a term the protocol fixes.
    __test__: ClassVar[bool] = False

    def __init__(self) -> None:
        self._entries: list[TestSetAccessEntry] = []

    def record(
        self,
        *,
        principal: str,
        protocol_digest: str,
        accessed_at: datetime,
        reason: str,
    ) -> TestSetAccessEntry:
        """Append one access and return the entry that was stored."""

        entry = TestSetAccessEntry(
            principal=principal,
            protocol_digest=protocol_digest,
            accessed_at=accessed_at,
            reason=reason,
        )
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> tuple[TestSetAccessEntry, ...]:
        """Every access in the order it was recorded."""

        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def to_rows(self) -> list[dict[str, str]]:
        """The log as flat string rows, ready for a later append-only store binding.

        Insertion order is preserved and not sorted: the order accesses happened in is part
        of what the log is for, and a row order derived from the data would lose it.
        """

        return [
            {
                "schema_version": entry.schema_version,
                "principal": entry.principal,
                "protocol_digest": entry.protocol_digest,
                "accessed_at": entry.accessed_at.isoformat(),
                "reason": entry.reason,
            }
            for entry in self._entries
        ]


_Node = tuple[str, str]


class _UnionFind:
    """Union-find over namespaced nodes, with ``min`` as the tie-break.

    Union by minimum rather than by rank: the trees are tiny, and making the representative
    a pure function of the node names means the structure — and therefore every split built
    on it — cannot depend on the order the edges arrived in.
    """

    def __init__(self) -> None:
        self._parent: dict[_Node, _Node] = {}

    def find(self, node: _Node) -> _Node:
        parent = self._parent.setdefault(node, node)
        while parent != self._parent[parent]:
            parent = self._parent[parent]
        # Path compression, written iteratively so a long ancestry chain cannot recurse.
        walker = node
        while walker != parent:
            self._parent[walker], walker = parent, self._parent[walker]
        return parent

    def union(self, left: _Node, right: _Node) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        winner, loser = sorted((left_root, right_root))
        self._parent[loser] = winner


def load_project_registry(path: Path = PROJECTS_PATH) -> ProjectRegistry:
    """Read and validate the development-project registry.

    Validation happens here rather than at the first use of a field so that a malformed
    corpus fails while it is still obviously a corpus problem, not several call frames later
    as a puzzling split.
    """

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return ProjectRegistry.model_validate(document)


def lineage_roots(projects: Sequence[ProjectLineage]) -> dict[str, str]:
    """Map every project id to its lineage root id.

    The root is the lexicographically smallest project id in the lineage, which makes it a
    stable name for the group rather than an accident of input order. The returned mapping
    is ordered by project id for the same reason.
    """

    union = _UnionFind()
    for project in projects:
        node: _Node = ("project", project.project_id)
        union.union(node, ("repository", project.repository_identity))
        union.union(node, ("family", project.task_family))
        for ancestor in project.ancestors:
            union.union(node, ("project", ancestor))

    members: dict[_Node, list[str]] = {}
    for project in projects:
        members.setdefault(union.find(("project", project.project_id)), []).append(
            project.project_id
        )

    roots: dict[str, str] = {}
    for project_ids in members.values():
        root = min(project_ids)
        for project_id in project_ids:
            roots[project_id] = root
    return {project_id: roots[project_id] for project_id in sorted(roots)}


def _root_order(root_ids: Sequence[str], *, seed: int) -> list[str]:
    """Lineage roots in seeded-hash order.

    The key is ``sha256("<seed>:<root_id>")`` and the root id itself is the tie-break, so
    the permutation is a pure function of the pair and reproduces on any machine and any
    Python build — which ``hash()`` and ``random.shuffle`` over an unsorted set would not.
    """

    def key(root_id: str) -> tuple[str, str]:
        digest = hashlib.sha256(f"{seed}:{root_id}".encode()).hexdigest()
        return (digest, root_id)

    return sorted(root_ids, key=key)


def _quotas(total: int, fractions: SplitFractions) -> dict[SplitName, int]:
    """How many lineage roots each split gets, by largest remainder, never starving one.

    Largest-remainder alone leaves a split empty whenever its share rounds below one — with
    seven roots and a 10% drift share, the drift holdout would simply not exist, and the
    suite would report four splits while claiming five. So after the remainder pass, any
    empty split takes one root from whichever split most exceeds its own raw share. That
    keeps the counts as close to the requested fractions as an integer allocation can be
    while making "all five splits are required" true of the result and not only of the
    request.
    """

    raw = {split: fractions.share_of(split) * total for split in SPLIT_ORDER}
    counts = {split: int(math.floor(raw[split])) for split in SPLIT_ORDER}
    remainder = total - sum(counts.values())
    by_remainder = sorted(
        SPLIT_ORDER, key=lambda split: (-(raw[split] - counts[split]), SPLIT_ORDER.index(split))
    )
    for split in by_remainder[:remainder]:
        counts[split] += 1

    if total >= len(SPLIT_ORDER):
        for split in SPLIT_ORDER:
            if counts[split] > 0:
                continue
            donors = [name for name in SPLIT_ORDER if counts[name] > 1]
            if not donors:
                break
            donor = min(
                donors,
                key=lambda name: (-(counts[name] - raw[name]), SPLIT_ORDER.index(name)),
            )
            counts[donor] -= 1
            counts[split] += 1
    return counts


def assign(
    roots: Mapping[str, str], *, fractions: SplitFractions, seed: int
) -> SplitAssignment:
    """Allocate whole lineages to the five splits.

    The unit of allocation is the lineage root, never the project. Splitting by project
    count would balance the splits better and would also put a fork in training and its
    upstream in the holdout, which is the exact leak the whole module exists to prevent, so
    the imbalance is accepted and the indivisibility is not negotiable.
    """

    if not roots:
        raise ValueError("cannot split an empty lineage map")

    members: dict[str, list[str]] = {}
    for project_id in sorted(roots):
        members.setdefault(roots[project_id], []).append(project_id)

    ordered_roots = _root_order(sorted(members), seed=seed)
    counts = _quotas(len(ordered_roots), fractions)

    allocated: dict[SplitName, list[str]] = {}
    cursor = 0
    for split in SPLIT_ORDER:
        taken = ordered_roots[cursor : cursor + counts[split]]
        cursor += counts[split]
        allocated[split] = sorted(
            project_id for root_id in taken for project_id in members[root_id]
        )

    return SplitAssignment(
        seed=seed,
        fractions=fractions,
        root_by_project={project_id: roots[project_id] for project_id in sorted(roots)},
        train_project_ids=allocated[SplitName.TRAIN],
        calibration_project_ids=allocated[SplitName.CALIBRATION],
        development_project_ids=allocated[SplitName.DEVELOPMENT],
        test_project_ids=allocated[SplitName.TEST],
        drift_project_ids=allocated[SplitName.DRIFT],
    )


def assert_disjoint(assignment: SplitAssignment) -> None:
    """Raise :class:`SplitViolation` unless the assignment leaks nothing.

    Three failures, checked in this order and reported together where they coexist:

    1. a project id in two splits — the cheap half of the guarantee, and the half
       :class:`~accretion.contracts.routing.SnapshotSplit` already knows how to state;
    2. a project in a split whose lineage root is unknown — not pedantry: clause 3 cannot
       see a leak it has no root for, so an unknown project is a hole in the check rather
       than a harmless extra;
    3. two projects sharing a lineage root but sitting in different splits — the half that
       actually catches the fork, the paper extension and the regenerated instance.
    """

    by_split = assignment.by_split()
    failures: list[str] = []

    placements: dict[str, list[SplitName]] = {}
    for split, project_ids in by_split.items():
        for project_id in project_ids:
            placements.setdefault(project_id, []).append(split)
    for project_id in sorted(placements):
        splits = placements[project_id]
        if len(splits) > 1:
            named = ", ".join(split.value for split in splits)
            failures.append(
                f"project {project_id!r} appears in the {named} splits; a project on two "
                "sides of a split leaks"
            )

    unknown = sorted(set(placements) - set(assignment.root_by_project))
    if unknown:
        failures.append(
            f"projects {unknown!r} are assigned to a split but have no lineage root, so "
            "their lineage cannot be checked for leakage"
        )

    for project_id in sorted(placements):
        root = assignment.root_by_project.get(project_id)
        if root is None:
            continue
        for other in sorted(placements):
            if other <= project_id or assignment.root_by_project.get(other) != root:
                continue
            first, second = placements[project_id][0], placements[other][0]
            if first is not second:
                failures.append(
                    f"projects {project_id!r} and {other!r} share lineage root {root!r} but "
                    f"are in the {first.value} and {second.value} splits; a lineage on two "
                    "sides of a split leaks"
                )

    if failures:
        raise SplitViolation("; ".join(failures))


def exact_duplicate_digests(records: Mapping[str, str]) -> dict[str, list[str]]:
    """Group record ids by content digest, keeping only the digests more than one record has.

    The first leakage control in the protocol's list, and the cheapest: two records with the
    same content digest are one piece of evidence however many project ids they arrived
    under, and counting them twice inflates both the training weight and the holdout score.
    """

    grouped: dict[str, list[str]] = {}
    for record_id in sorted(records):
        grouped.setdefault(records[record_id], []).append(record_id)
    return {
        digest: sorted(grouped[digest])
        for digest in sorted(grouped)
        if len(grouped[digest]) > 1
    }


def _tokens(text: str) -> frozenset[str]:
    """Lowercase alphanumeric tokens. Punctuation, case and spacing are not similarity."""

    return frozenset(_TOKEN.findall(text.lower()))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Token Jaccard similarity. Two empty token sets are ``0.0``, not ``1.0``.

    Defining the empty-empty case as identical would make every objective with no
    alphanumeric content a near-duplicate of every other, which is a flood of findings about
    nothing.
    """

    first, second = frozenset(left), frozenset(right)
    union = first | second
    if not union:
        return 0.0
    return len(first & second) / len(union)


def near_duplicate_objectives(
    texts: Mapping[str, str], threshold: float
) -> list[NearDuplicatePair]:
    """Objective pairs whose token Jaccard similarity is at least ``threshold``.

    The threshold is inclusive: a pair sitting exactly on the boundary is reported, because
    the boundary is chosen as the point at which a pair is *already* too similar to be
    treated as independent evidence.

    Results are ordered by descending similarity and then by id, so a caller reviewing the
    first ten sees the ten worst and sees the same ten on every run.
    """

    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be within [0, 1], got {threshold!r}")

    tokenised = {text_id: _tokens(texts[text_id]) for text_id in sorted(texts)}
    identifiers = sorted(tokenised)
    pairs: list[NearDuplicatePair] = []
    for index, left_id in enumerate(identifiers):
        for right_id in identifiers[index + 1 :]:
            similarity = jaccard(tokenised[left_id], tokenised[right_id])
            if similarity >= threshold:
                pairs.append(
                    NearDuplicatePair(
                        left_id=left_id, right_id=right_id, similarity=similarity
                    )
                )
    return sorted(pairs, key=lambda pair: (-pair.similarity, pair.left_id, pair.right_id))
