"""The locked corpus and the drift holdout are what their seeds say they are.

A locked test set is only worth having if three things are true of it, and each of them is a
way the whole exercise could be theatre rather than evidence:

**It is reproducible.** ``evals/router/locked`` and ``evals/router/drift`` are byte-for-byte
what :mod:`tests.router_corpus_generator` produces from their seeds, so "we generated a fresh
corpus" is checkable rather than asserted. A committed corpus nobody can regenerate is
indistinguishable from a corpus that was edited until the result looked good, which is the
argument the shipped corpus's own byte test already makes; this file makes it twice more, for
the two corpora where it matters most.

**It is the frozen size.** Pre-registration item 1 froze 18 trials per cell and six projects
per half. A corpus quietly generated at the development corpus's two trials would still load,
still run and still produce a plausible table — and would be under-powered by a factor of nine
against the size the pre-registration was sized at. So the cell census is asserted exhaustively
rather than by a total count, which a corpus with one fat cell and one thin one would pass.

**It is disjoint — from the development corpus and from the other holdout.** Disjointness is
asserted at the level of *lineage* and not project id: the three registries are merged and run
through :func:`~accretion.routing.split.lineage_roots`, so a derived corpus that had kept a
repository digest would be united with the corpus everybody iterated on and would fail here
rather than silently reporting a number about data it had already seen.

And one thing specific to the holdout: every drift lineage is provider era ``2026-H2`` and
every drift runtime is a minor version later than the locked one, so the two corpora differ at
both levels pre-registration item 8 names — the coarse label and the fine serving window. A
holdout that moved only the label would differ in the level nothing reads.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from router_corpus_generator import (
    DRIFT_OPTIONS,
    DRIFT_PROVIDER_ERA,
    FROZEN_TRIALS_PER_CELL,
    LOCKED_OPTIONS,
    PREREGISTRATION_SHA256,
    build,
    write,
)

from accretion.router_benchmark import CORPUS_ROOT, RouterBenchmarkCorpus
from accretion.routing.locked_test import DRIFT_CORPUS_ROOT, LOCKED_CORPUS_ROOT
from accretion.routing.split import ProjectRegistry, lineage_roots, load_project_registry

CORPORA = {
    "locked": (LOCKED_CORPUS_ROOT, LOCKED_OPTIONS),
    "drift": (DRIFT_CORPUS_ROOT, DRIFT_OPTIONS),
}


def setup_regenerated(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Rebuild one corpus from its declared options and return (regenerated, committed) roots.

    The options come from the generator's own :data:`LOCKED_OPTIONS` / :data:`DRIFT_OPTIONS`
    rather than from literals repeated here. Repeating them would make this a test of a copy of
    the generator against the generator, which passes whatever either of them says.
    """

    committed, options = CORPORA[name]
    regenerated = tmp_path / name
    write(regenerated, **options)
    return regenerated, committed


def test_the_locked_corpus_is_exactly_what_its_seed_generates(tmp_path: Path) -> None:
    regenerated, committed = setup_regenerated(tmp_path, "locked")
    for stem in build(**LOCKED_OPTIONS):
        name = f"{stem}.json"
        assert (regenerated / name).read_bytes() == (committed / name).read_bytes(), (
            f"evals/router/locked/{name} is not what seed {LOCKED_OPTIONS['seed']} produces; "
            "regenerate with `python -m tests.router_corpus_generator` and say in the pull "
            "request why the locked corpus moved"
        )


def test_the_drift_corpus_is_exactly_what_its_seed_generates(tmp_path: Path) -> None:
    regenerated, committed = setup_regenerated(tmp_path, "drift")
    for stem in build(**DRIFT_OPTIONS):
        name = f"{stem}.json"
        assert (regenerated / name).read_bytes() == (committed / name).read_bytes(), (
            f"evals/router/drift/{name} is not what seed {DRIFT_OPTIONS['seed']} produces; "
            "regenerate with `python -m tests.router_corpus_generator` and say in the pull "
            "request why the drift holdout moved"
        )


def test_both_corpora_carry_eighteen_trials_in_every_cell_of_a_complete_grid() -> None:
    """Item 1's frozen size, asserted cell by cell rather than as a total.

    A total would be satisfied by a corpus with one 36-trial cell and one empty one, which is
    exactly the shape a partially regenerated corpus has.
    """

    for name in CORPORA:
        corpus = RouterBenchmarkCorpus.load(CORPORA[name][0])
        assert len(corpus.tasks) == 36, name
        assert len(corpus.candidates) == 6, name
        cells = Counter((trace.task_id, trace.candidate_id) for trace in corpus.traces)
        assert len(cells) == 36 * 6, name
        assert set(cells.values()) == {FROZEN_TRIALS_PER_CELL}, name
        assert len(corpus.traces) == 36 * 6 * FROZEN_TRIALS_PER_CELL, name
        # Six projects a side, as item 1 also froze; the halves are what the estimands
        # are computed over and a lopsided split would silently re-weight them.
        split = corpus.config.selection_split
        assert len(split.selection_project_ids) == 6, name
        assert len(split.evaluation_project_ids) == 6, name


def test_no_lineage_is_shared_between_the_three_corpora() -> None:
    """The development registry, the locked registry and the drift registry share no lineage.

    Merged and re-rooted rather than compared pairwise by project id, because the leak this
    guards against does not look like a repeated id: it looks like a derived corpus that kept
    a repository digest or a task family, which :func:`lineage_roots` unions into one lineage
    while every id stays distinct.
    """

    registries = {
        "development": load_project_registry(),
        "locked": RouterBenchmarkCorpus.load(LOCKED_CORPUS_ROOT).project_registry(),
        "drift": RouterBenchmarkCorpus.load(DRIFT_CORPUS_ROOT).project_registry(),
    }
    origin = {
        project.project_id: name
        for name, registry in registries.items()
        for project in registry.projects
    }
    # Twelve projects each, and no id claimed twice: ``ProjectRegistry`` refuses duplicates,
    # so validating the merge is itself the id-level half of the assertion.
    assert [len(registry.projects) for registry in registries.values()] == [12, 12, 12]
    merged = ProjectRegistry(
        projects=[project for registry in registries.values() for project in registry.projects]
    )
    assert len(origin) == 36

    by_root: dict[str, set[str]] = {}
    for project_id, root in lineage_roots(merged.projects).items():
        by_root.setdefault(root, set()).add(origin[project_id])
    straddling = sorted(root for root, names in by_root.items() if len(names) > 1)
    assert straddling == [], (
        f"lineages {straddling!r} span two corpora; a locked corpus that shares a lineage "
        "with the corpus everybody iterated on is not a locked corpus"
    )
    # Non-vacuity: the merge really does contain lineages with more than one member, so the
    # assertion above is about lineages and not about a map of singletons.
    assert max(Counter(lineage_roots(merged.projects).values()).values()) > 1


def test_every_drift_lineage_is_the_later_provider_era_and_every_runtime_is_later() -> None:
    """Item 8's two levels both move: the coarse ``provider_era`` and the fine serving window."""

    locked = RouterBenchmarkCorpus.load(LOCKED_CORPUS_ROOT)
    drift = RouterBenchmarkCorpus.load(DRIFT_CORPUS_ROOT)

    drift_eras = {
        project.labels["provider_era"] for project in drift.project_registry().projects
    }
    assert drift_eras == {DRIFT_PROVIDER_ERA}
    # The locked corpus keeps the development corpus's mixture, so "all one era" is a property
    # of the holdout and not of every derived corpus.
    locked_eras = {
        project.labels["provider_era"] for project in locked.project_registry().projects
    }
    assert locked_eras == {"2026-H1", DRIFT_PROVIDER_ERA}

    locked_versions = {c.candidate_id: c.runtime_version for c in locked.candidates}
    drift_versions = {c.candidate_id: c.runtime_version for c in drift.candidates}
    assert sorted(locked_versions) == sorted(drift_versions)
    for candidate_id, version in locked_versions.items():
        major, minor, patch = version.split(".")
        assert drift_versions[candidate_id] == f"{major}.{int(minor) + 1}.{patch}"
    # The serving windows are disjoint: no (provider, runtime version, model) triple appears
    # in both corpora, which is what makes the holdout a different window and not a relabelling.
    def windows(corpus: RouterBenchmarkCorpus) -> set[tuple[str, str, str]]:
        return {(c.provider.value, c.runtime_version, c.model_id) for c in corpus.candidates}

    assert windows(locked).isdisjoint(windows(drift))


def test_both_corpora_pin_the_frozen_preregistration_and_the_one_registered_ablation_table() -> (
    None
):
    """Neither holdout may be read against a different protocol or a different §14 table."""

    shipped = RouterBenchmarkCorpus.load()
    for name, (root, _options) in CORPORA.items():
        corpus = RouterBenchmarkCorpus.load(root)
        assert corpus.config.preregistration_sha256 == PREREGISTRATION_SHA256, name
        assert corpus.config.preregistration_sha256 == shipped.config.preregistration_sha256, name
        assert corpus.config.ablations_path == shipped.config.ablations_path, name
        # The reviewed relative line resolves to the one committed table from a nested corpus
        # root too; resolving it by counting parents above the corpus would not.
        assert corpus.ablations_path == CORPUS_ROOT / "ablations.v1.json", name
        assert corpus.ablations_path is not None and corpus.ablations_path.is_file(), name
        # Different corpora, and therefore different run ids: a locked result can never be
        # mistaken for a development result in a report that quotes the run id.
        assert corpus.run_id != shipped.run_id, name


def test_the_locked_and_drift_corpora_are_two_different_samples_of_one_designed_world() -> None:
    """Same structure, different draws — which is the only thing a new seed is allowed to mean.

    The declared half of the corpus (eligibility, cost points, the registered oracle subset,
    the v0.1 table, the weights and both gate thresholds) must be identical to the shipped
    corpus's, or a difference between the development numbers and the locked ones would be a
    difference in the experiment rather than in the sample. The sampled half must differ, or
    the "new seed" was not new.
    """

    shipped = RouterBenchmarkCorpus.load()
    locked = RouterBenchmarkCorpus.load(LOCKED_CORPUS_ROOT)
    drift = RouterBenchmarkCorpus.load(DRIFT_CORPUS_ROOT)

    for corpus in (locked, drift):
        assert corpus.config.weights == shipped.config.weights
        assert corpus.config.invalid_action_penalty == shipped.config.invalid_action_penalty
        assert corpus.config.latency_budget_ms == shipped.config.latency_budget_ms
        assert corpus.config.verified_success_floor == shipped.config.verified_success_floor
        assert corpus.config.false_acceptance_ceiling == shipped.config.false_acceptance_ceiling
        assert corpus.config.oracle_candidate_subset == shipped.config.oracle_candidate_subset
        assert corpus.config.deterministic_v01_table == shipped.config.deterministic_v01_table
        assert corpus.config.configuration_version == shipped.config.configuration_version
        assert {c.candidate_id for c in corpus.candidates} == {
            c.candidate_id for c in shipped.candidates
        }
        by_id = sorted(corpus.candidates, key=lambda item: item.candidate_id)
        shipped_by_id = sorted(shipped.candidates, key=lambda item: item.candidate_id)
        assert [item.eligible_node_classes for item in by_id] == [
            item.eligible_node_classes for item in shipped_by_id
        ]
        assert Counter(task.node_class for task in corpus.tasks) == Counter(
            task.node_class for task in shipped.tasks
        )

    assert {corpus.config.seed for corpus in (shipped, locked, drift)} == {
        shipped.config.seed,
        LOCKED_OPTIONS["seed"],
        DRIFT_OPTIONS["seed"],
    }
    assert len({corpus.trace_sha256 for corpus in (shipped, locked, drift)}) == 3


def test_the_derived_registries_are_committed_beside_their_corpora() -> None:
    """A derived corpus carries the registry its split is proved against; the shipped one does not.

    The shipped corpus's registry is hand-authored and shared with
    ``tests/test_v04_m10_split.py``;
    the derived ones are generated. Asserting that the generator emits a registry only under a
    project prefix is what stops a future regeneration of the development corpus rewriting the
    hand-authored file as a side effect.
    """

    assert "projects.v1" not in build()
    assert "projects.v1" in build(**LOCKED_OPTIONS)
    for name, (root, _options) in CORPORA.items():
        document = json.loads((root / "projects.v1.json").read_text(encoding="utf-8"))
        assert document["suite_version"] == "v1", name
        assert [entry["project_id"] for entry in document["projects"]] == [
            entry.project_id
            for entry in RouterBenchmarkCorpus.load(root).project_registry().projects
        ], name
        assert all(
            entry["project_id"].startswith(f"prj-router-{name}-")
            for entry in document["projects"]
        ), name
