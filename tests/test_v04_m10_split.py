"""The project-lineage split enforcer: lineages are indivisible and holdouts stay clean.

Four claims are under test, and each of them is a way the router benchmark could report a
number nobody should believe.

**A lineage is found even when nobody declared it.** Forks share a repository digest,
generated instances share a task family, and paper extensions share only a declaration.
Each of those three edges is exercised on its own, so dropping any one of them is a red test
rather than a quietly weaker enforcer.

**A lineage is never divided.** The property test builds two hundred seeded registries, each
one carrying injected fork pairs, family collisions and ancestry chains, and requires that
every pair of projects sharing a root also shares a split. It also states the two guards
that keep it from passing vacuously: every generated registry really does contain a
multi-project lineage, and most of them really do have enough roots to fill five splits.

**The enforcer says what is wrong.** ``assert_disjoint`` raises naming the projects, the
lineage root and the splits involved, for a repeated project, a straddling lineage and a
project whose lineage is unknown — the last one because a check that skips what it cannot
resolve is a check with a hole in it.

**The shipped corpus is a corpus and not a placeholder.** ``evals/router/projects.v1.json``
is loaded, its two fork pairs and its three-link paper chain are asserted by structure, and
all five splits — the drift holdout included — come out non-empty across fifty seeds.

Everything here is offline, seeded and clock-free: the only timestamps are frozen literals
handed to the access log, which never reads a clock of its own.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from accretion.routing.split import (
    DEFAULT_FRACTIONS,
    SPLIT_ORDER,
    ProjectLineage,
    SplitAssignment,
    SplitFractions,
    SplitName,
    SplitViolation,
    TestSetAccessLog,
    assert_disjoint,
    assign,
    exact_duplicate_digests,
    lineage_roots,
    load_project_registry,
    near_duplicate_objectives,
)

FROZEN_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def digest(value: object) -> str:
    """A stable 64-hex repository identity for a synthetic project."""

    return hashlib.sha256(str(value).encode()).hexdigest()


def project(
    project_id: str,
    *,
    repository: str,
    family: str,
    ancestors: list[str] | None = None,
) -> ProjectLineage:
    return ProjectLineage(
        project_id=project_id,
        repository_identity=repository,
        task_family=family,
        ancestors=list(ancestors or []),
        labels={},
    )


def random_registry(seed: int) -> list[ProjectLineage]:
    """A synthetic registry whose lineages are injected rather than hoped for.

    Every project starts with its own repository and its own family, and the three edge
    kinds are then added deliberately: one to three fork pairs, up to two family collisions,
    and one to three ancestry links. Drawing repositories and families from a small shared
    pool instead would collapse almost every registry into a single lineage, and a property
    that only ever sees one group proves nothing about keeping groups apart.
    """

    rng = random.Random(seed)
    count = rng.randint(8, 20)
    rows = [
        {
            "project_id": f"prj-{index:02d}",
            "repository_identity": digest(("repo", index)),
            "task_family": f"family-{index:02d}",
            "ancestors": [],
        }
        for index in range(count)
    ]
    for _ in range(rng.randint(1, 3)):
        upstream, fork = rng.sample(range(count), 2)
        rows[fork]["repository_identity"] = rows[upstream]["repository_identity"]
    for _ in range(rng.randint(0, 2)):
        first, second = rng.sample(range(count), 2)
        rows[second]["task_family"] = rows[first]["task_family"]
    for _ in range(rng.randint(1, 3)):
        ancestor, descendant = rng.sample(range(count), 2)
        ancestors = rows[descendant]["ancestors"]
        assert isinstance(ancestors, list)
        if rows[ancestor]["project_id"] not in ancestors:
            ancestors.append(rows[ancestor]["project_id"])
    return [ProjectLineage.model_validate(row) for row in rows]


def groups_of(roots: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for project_id in sorted(roots):
        grouped[roots[project_id]].append(project_id)
    return dict(grouped)


def test_forks_and_paper_extensions_share_a_lineage_root() -> None:
    """Each of the three edge kinds merges a lineage on its own.

    The three pairs are built so that exactly one edge could have joined them: the fork pair
    shares only a repository digest, the generated pair shares only a task family, and the
    paper chain shares neither and only declares its ancestry. A fourth pair shares nothing
    but an ancestor the registry does not contain, which must still merge them — two
    projects derived from the same absent upstream are one lineage whether or not the corpus
    happens to include it.
    """

    projects = [
        # Fork pair: one repository, two families, no declaration.
        project("prj-fork-upstream", repository=DIGEST_A, family="fork-upstream-family"),
        project("prj-fork-downstream", repository=DIGEST_A, family="fork-downstream-family"),
        # Generated instances: two repositories, one family.
        project("prj-generated-one", repository=digest("generated-one"), family="generated"),
        project("prj-generated-two", repository=digest("generated-two"), family="generated"),
        # Paper chain: nothing shared but the declaration.
        project("prj-paper-base", repository=digest("paper-base"), family="paper-base"),
        project(
            "prj-paper-ext",
            repository=digest("paper-ext"),
            family="paper-ext",
            ancestors=["prj-paper-base"],
        ),
        project(
            "prj-paper-bench",
            repository=digest("paper-bench"),
            family="paper-bench",
            ancestors=["prj-paper-ext"],
        ),
        # Two descendants of an upstream project this registry does not contain.
        project(
            "prj-absent-child-one",
            repository=digest("absent-one"),
            family="absent-one",
            ancestors=["prj-not-in-this-registry"],
        ),
        project(
            "prj-absent-child-two",
            repository=digest("absent-two"),
            family="absent-two",
            ancestors=["prj-not-in-this-registry"],
        ),
        # A project sharing nothing with anything.
        project("prj-alone", repository=DIGEST_B, family="alone"),
    ]

    roots = lineage_roots(projects)

    assert sorted(groups_of(roots).values()) == sorted(
        [
            ["prj-absent-child-one", "prj-absent-child-two"],
            ["prj-alone"],
            ["prj-fork-downstream", "prj-fork-upstream"],
            ["prj-generated-one", "prj-generated-two"],
            ["prj-paper-base", "prj-paper-bench", "prj-paper-ext"],
        ]
    )
    assert roots["prj-fork-downstream"] == roots["prj-fork-upstream"]
    assert roots["prj-generated-one"] == roots["prj-generated-two"]
    assert roots["prj-paper-bench"] == roots["prj-paper-base"]
    assert roots["prj-absent-child-one"] == roots["prj-absent-child-two"]
    # The root is the smallest member id, so the group has a stable name.
    assert roots["prj-paper-bench"] == "prj-paper-base"
    assert roots["prj-alone"] == "prj-alone"
    # Reordering the input cannot reorder the answer.
    assert lineage_roots(list(reversed(projects))) == roots


def test_a_lineage_never_straddles_two_splits() -> None:
    """Two hundred seeded registries, and no lineage divided in any of them.

    The mutation this is aimed at is assigning by project instead of by root: with the
    lineage map replaced by an identity map, a fork lands in training while its upstream
    lands in the drift holdout. The two counters at the end are the guards that stop the
    property passing vacuously — a generator that produced only singleton lineages, or only
    registries too small to fill five splits, would satisfy the loop while proving nothing.
    """

    registries_with_a_shared_root = 0
    registries_with_five_or_more_roots = 0

    for seed in range(200):
        projects = random_registry(seed)
        roots = lineage_roots(projects)
        grouped = groups_of(roots)
        if any(len(members) > 1 for members in grouped.values()):
            registries_with_a_shared_root += 1

        assignment = assign(roots, fractions=DEFAULT_FRACTIONS, seed=seed)
        assert_disjoint(assignment)

        placed = [
            project_id
            for split in SPLIT_ORDER
            for project_id in assignment.project_ids_for(split)
        ]
        assert sorted(placed) == sorted(roots), seed
        assert len(placed) == len(set(placed)), seed

        for members in grouped.values():
            splits = {assignment.split_of(project_id) for project_id in members}
            assert len(splits) == 1, (seed, sorted(members), splits)

        if len(grouped) >= 5:
            registries_with_five_or_more_roots += 1
            # Every split is required, so an allocation with room for five is never short.
            assert all(assignment.project_ids_for(split) for split in SPLIT_ORDER), seed

    assert registries_with_a_shared_root == 200
    assert registries_with_five_or_more_roots >= 150


def test_assert_disjoint_names_the_offending_project_and_splits() -> None:
    """Three leaks, each reported with the ids and splits a reviewer would need.

    ``prj-solo`` is deliberately a singleton lineage in the first case: if the repeated-
    project check were removed, the shared-root check must not accidentally cover for it,
    or the two halves of the guarantee would be untestable apart.
    """

    lineage = {"prj-solo": "prj-solo", "prj-a": "prj-a", "prj-b": "prj-a"}

    clean = SplitAssignment(
        seed=1,
        fractions=DEFAULT_FRACTIONS,
        root_by_project=lineage,
        train_project_ids=["prj-a", "prj-b"],
        test_project_ids=["prj-solo"],
    )
    assert assert_disjoint(clean) is None

    repeated = SplitAssignment(
        seed=1,
        fractions=DEFAULT_FRACTIONS,
        root_by_project=lineage,
        train_project_ids=["prj-a", "prj-b", "prj-solo"],
        test_project_ids=["prj-solo"],
    )
    with pytest.raises(SplitViolation) as repeated_error:
        assert_disjoint(repeated)
    assert "prj-solo" in str(repeated_error.value)
    assert "TRAIN" in str(repeated_error.value)
    assert "TEST" in str(repeated_error.value)

    straddling = SplitAssignment(
        seed=1,
        fractions=DEFAULT_FRACTIONS,
        root_by_project=lineage,
        train_project_ids=["prj-a", "prj-solo"],
        drift_project_ids=["prj-b"],
    )
    with pytest.raises(SplitViolation) as straddle_error:
        assert_disjoint(straddling)
    message = str(straddle_error.value)
    assert "prj-a" in message
    assert "prj-b" in message
    assert "TRAIN" in message
    assert "DRIFT" in message
    assert "prj-solo" not in message

    unrooted = SplitAssignment(
        seed=1,
        fractions=DEFAULT_FRACTIONS,
        root_by_project={"prj-a": "prj-a"},
        train_project_ids=["prj-a"],
        test_project_ids=["prj-orphan"],
    )
    with pytest.raises(SplitViolation) as unrooted_error:
        assert_disjoint(unrooted)
    assert "prj-orphan" in str(unrooted_error.value)


def test_assignment_is_seed_deterministic() -> None:
    """The same seed reproduces the split; a different seed moves it; input order never does."""

    roots = lineage_roots(load_project_registry().projects)

    first = assign(roots, fractions=DEFAULT_FRACTIONS, seed=20260905)
    second = assign(roots, fractions=DEFAULT_FRACTIONS, seed=20260905)
    assert first.model_dump() == second.model_dump()
    assert first.seed == 20260905

    shuffled = dict(sorted(roots.items(), reverse=True))
    assert list(shuffled) != list(roots)
    reordered = assign(shuffled, fractions=DEFAULT_FRACTIONS, seed=20260905)
    assert reordered.model_dump() == first.model_dump()

    other = assign(roots, fractions=DEFAULT_FRACTIONS, seed=20260906)
    assert other.by_split() != first.by_split()

    # A different seed is still a valid split, not merely a different one.
    assert_disjoint(other)
    assert sorted(other.root_by_project) == sorted(first.root_by_project)


def test_drift_split_is_required_and_non_empty_for_the_shipped_corpus() -> None:
    """The temporal/provider-drift holdout exists at every seed, and cannot be waived.

    Asserting it over fifty seeds rather than one is the point: a drift share that rounds
    below a single lineage would leave the split empty for most seeds and pass for a lucky
    one, and a suite that reported four splits while claiming five is exactly the failure
    the program plan promoted this split to prevent.
    """

    roots = lineage_roots(load_project_registry().projects)

    for seed in range(50):
        assignment = assign(roots, fractions=DEFAULT_FRACTIONS, seed=seed)
        for split in SPLIT_ORDER:
            assert assignment.project_ids_for(split), (seed, split)
        sealed = assignment.to_sealed()
        assert set(assignment.project_ids_for(SplitName.DRIFT)) <= set(
            sealed.holdout_project_ids
        )
        assert set(assignment.project_ids_for(SplitName.TEST)) <= set(
            sealed.holdout_project_ids
        )
        assert set(assignment.project_ids_for(SplitName.DEVELOPMENT)) <= set(
            sealed.validation_project_ids
        )
        assert sorted(sealed.training_project_ids) == sorted(
            assignment.project_ids_for(SplitName.TRAIN)
        )

    with pytest.raises(ValidationError):
        SplitFractions(train=0.6, calibration=0.15, development=0.15, test=0.1, drift=0.0)
    with pytest.raises(ValidationError):
        SplitFractions(train=0.5, calibration=0.15, development=0.15, test=0.1, drift=0.2)


def test_near_duplicate_objectives_are_flagged_above_the_threshold_only() -> None:
    """Token Jaccard, punctuation-blind, inclusive at the threshold and silent below it."""

    texts = {
        "obj-base": "Refactor the alpha beta gamma delta service",
        "obj-restyled": "refactor, THE alpha; beta -- gamma delta service!",
        "obj-near": "Refactor the alpha beta gamma epsilon service",
        "obj-far": "Draft the quarterly financial report",
        "obj-empty": "!!! ???",
    }

    at_six_tenths = near_duplicate_objectives(texts, 0.6)
    flagged = [(pair.left_id, pair.right_id) for pair in at_six_tenths]
    assert flagged == [
        ("obj-base", "obj-restyled"),
        ("obj-base", "obj-near"),
        ("obj-near", "obj-restyled"),
    ]
    assert at_six_tenths[0].similarity == 1.0
    assert at_six_tenths[1].similarity == pytest.approx(0.75)

    # The boundary is inclusive, and one notch above it drops the pair sitting on it.
    boundary = min(pair.similarity for pair in at_six_tenths)
    assert near_duplicate_objectives(texts, boundary) == at_six_tenths
    tighter = near_duplicate_objectives(texts, boundary + 1e-9)
    assert [(pair.left_id, pair.right_id) for pair in tighter] == [
        ("obj-base", "obj-restyled")
    ]

    # An objective with no tokens is a near duplicate of nothing, not of everything.
    assert all(
        "obj-empty" not in (pair.left_id, pair.right_id)
        for pair in near_duplicate_objectives(texts, 0.05)
    )
    assert all(
        "obj-far" not in (pair.left_id, pair.right_id)
        for pair in near_duplicate_objectives(texts, 0.3)
    )

    with pytest.raises(ValueError):
        near_duplicate_objectives(texts, 1.5)

    # The cheaper control beside it: identical digests are one piece of evidence.
    assert exact_duplicate_digests(
        {"rec-1": DIGEST_A, "rec-2": DIGEST_B, "rec-3": DIGEST_A}
    ) == {DIGEST_A: ["rec-1", "rec-3"]}


def test_access_log_is_append_only() -> None:
    """The test-set access log grows, keeps its order, and hands out nothing writable."""

    log = TestSetAccessLog()
    log.record(
        principal="usr_analyst",
        protocol_digest=DIGEST_A,
        accessed_at=FROZEN_AT,
        reason="promotion evaluation",
    )
    log.record(
        principal="usr_reviewer",
        protocol_digest=DIGEST_B,
        accessed_at=FROZEN_AT.replace(hour=13),
        reason="audit of the promotion evaluation",
    )
    first_view = log.entries

    assert isinstance(first_view, tuple)
    assert [entry.principal for entry in first_view] == ["usr_analyst", "usr_reviewer"]
    assert not any(
        hasattr(log, name) for name in ("clear", "pop", "remove", "delete", "truncate")
    )

    log.record(
        principal="usr_analyst",
        protocol_digest=DIGEST_A,
        accessed_at=FROZEN_AT.replace(hour=14),
        reason="second read of the locked test set",
    )

    assert len(first_view) == 2, "a view taken earlier must not grow behind the caller"
    assert len(log) == 3
    assert [entry.principal for entry in log.entries] == [
        "usr_analyst",
        "usr_reviewer",
        "usr_analyst",
    ]
    assert log.to_rows() == [
        {
            "schema_version": "1.0",
            "principal": "usr_analyst",
            "protocol_digest": DIGEST_A,
            "accessed_at": "2026-09-05T12:00:00+00:00",
            "reason": "promotion evaluation",
        },
        {
            "schema_version": "1.0",
            "principal": "usr_reviewer",
            "protocol_digest": DIGEST_B,
            "accessed_at": "2026-09-05T13:00:00+00:00",
            "reason": "audit of the promotion evaluation",
        },
        {
            "schema_version": "1.0",
            "principal": "usr_analyst",
            "protocol_digest": DIGEST_A,
            "accessed_at": "2026-09-05T14:00:00+00:00",
            "reason": "second read of the locked test set",
        },
    ]


def test_shipped_projects_file_validates_and_is_disjoint() -> None:
    """``evals/router/projects.v1.json`` is a real corpus: forks, a chain, and a clean split.

    The structural assertions are what stop the file decaying into a list of unrelated
    projects. A corpus with no fork pair and no ancestry chain would let every test above
    pass while proving nothing about the lineages the enforcer exists to keep together.
    """

    registry = load_project_registry()

    assert registry.suite_version == "v1"
    assert len(registry.projects) == 12
    assert len({item.project_id for item in registry.projects}) == 12

    shared_repositories = {
        identity: sorted(
            item.project_id for item in registry.projects if item.repository_identity == identity
        )
        for identity in {item.repository_identity for item in registry.projects}
    }
    fork_pairs = sorted(
        members for members in shared_repositories.values() if len(members) == 2
    )
    assert fork_pairs == [
        ["prj-router-etl-batch", "prj-router-etl-batch-fork"],
        ["prj-router-web-api", "prj-router-web-api-fork"],
    ]
    # One fork pair declares no ancestry at all: the repository digest is the only edge.
    undeclared = next(
        item for item in registry.projects if item.project_id == "prj-router-web-api-fork"
    )
    assert undeclared.ancestors == []

    roots = lineage_roots(registry.projects)
    grouped = groups_of(roots)
    assert len(grouped) == 7
    assert sorted(grouped[roots["prj-router-paper-baseline"]]) == [
        "prj-router-paper-baseline",
        "prj-router-paper-benchmark",
        "prj-router-paper-extension",
    ]
    assert roots["prj-router-cli-scaffold"] == roots["prj-router-cli-generator"]

    assignment = assign(roots, fractions=DEFAULT_FRACTIONS, seed=11)
    assert_disjoint(assignment)
    sealed = assignment.to_sealed()
    assert sorted(
        sealed.training_project_ids
        + sealed.validation_project_ids
        + sealed.holdout_project_ids
    ) == sorted(item.project_id for item in registry.projects)
