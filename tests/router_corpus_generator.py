"""The seeded generator behind ``evals/router/*.v1.json``.

The router benchmark's development corpus is synthetic, and a synthetic corpus that nobody
can regenerate is indistinguishable from a corpus that was edited until the result looked
good. So the numbers in ``candidates``, ``tasks`` and ``replay-traces`` come from this one
seeded function, the shipped files are its output byte for byte, and a test asserts exactly
that. Changing a trace value therefore means changing the generator and saying why, which is
the review conversation a benchmark corpus should force.

It lives in ``tests/`` and not in ``src/`` deliberately. Nothing the package ships may need
it: the runtime **reads** the corpus and never rebuilds it, so shipping the generator would
put a source of benchmark data inside the product, where a future caller could regenerate a
corpus at run time and quietly move the baseline. Run it as a module to rewrite the files::

    PYTHONPATH=src python -m tests.router_corpus_generator

**Three corpora from one design.** That command writes the development corpus in
``evals/router`` and, beside it, the two nobody iterates on: ``evals/router/locked`` (seed
:data:`LOCKED_SEED`, the pre-registration's frozen 18 trials per cell) and
``evals/router/drift`` (seed :data:`DRIFT_SEED`, the same size, every lineage in provider era
``2026-H2`` and every runtime a minor version later). They are built by the same function with
different keyword arguments rather than by a second generator, because the only defensible
claim about a locked test set is that it is the *same experiment* at a seed nobody has seen —
and two functions that were supposed to agree would eventually not.

**The shape of the fiction.** Twelve development projects (``evals/router/projects.v1.json``,
merged with M10a's split enforcer) get three nodes each, so 36 tasks across four node
classes. Six configurations span three providers, four cost points and four eligibility
profiles, chosen so that no single configuration serves every node class well: the strongest
one is also the most expensive, the cheapest one is admissible almost nowhere, and two
mid-priced ones each win on a different class. That is the shape that makes routing worth
measuring — if one configuration dominated, the oracle gap would be zero and nothing about a
router would be interesting.

**What the seed does and does not decide.** Eligibility, the cost points and the per-class
quality means are declared, not sampled: they are the structure of the experiment. The seed
decides only the noise — per-trial quality jitter, latency, which trials verify, the rare
false acceptance, the planner's recorded choice and the runtime-health telemetry. So a
different seed gives a different sample of the same designed world, which is what a seed is
supposed to mean.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "evals" / "router"

PROJECTS_PATH = CORPUS_ROOT / "projects.v1.json"
"""The hand-authored development registry every derived corpus's registry is derived *from*.

Read rather than restated so that a locked corpus is provably the same designed world as the
development corpus — two fork pairs, one three-link chain, two standalones per half — with a
different seed, size, naming and provider era. Restating the structure here would let the two
drift apart silently, and "the locked corpus is the shipped corpus's world at a new seed" is
the only claim that makes measuring on it meaningful."""

LOCKED_ROOT = CORPUS_ROOT / "locked"
DRIFT_ROOT = CORPUS_ROOT / "drift"
"""The two corpora nobody iterates on. Directories and not files because a corpus **is** a
directory of four documents plus its registry (ADR4-M10-004): :class:`~accretion.\
router_benchmark.SelectionSplit` forbids extras, so there is no place in one document to put a
second split."""

SEED = 20260905
"""The one seed. Written into ``config.v1.json`` so a reader can rerun this file."""

LOCKED_SEED = 20260906
DRIFT_SEED = 20260907
"""New seeds, not new worlds. The locked corpus and the drift holdout sample the same declared
structure — the same eligibility, the same cost points, the same per-class quality means — so a
difference between the shipped numbers and the locked ones is a difference in the *sample* and
never in the experiment. Two different seeds because a drift holdout drawn from the locked
corpus's own noise would be a second reading of the locked corpus."""

FROZEN_TRIALS_PER_CELL = 18
"""Pre-registration item 1's frozen size: 18 tasks × 18 trials = 324 paired units per
configuration, against the 295 that δ = 0.02 needs at the pilot's evaluation-half σ of 0.0867.
The OQ-409 floor of nine is cleared twice over."""

DRIFT_PROVIDER_ERA = "2026-H2"
"""Every drift lineage's ``labels.provider_era``. The locked corpus keeps the shipped mixture
of ``2026-H1`` and ``2026-H2``; the holdout is entirely the later era, which is what makes it a
*drift* holdout rather than a second sample."""

DRIFT_RUNTIME_MINOR_BUMP = 1
"""How far the drift corpus's runtimes move, in minor versions. Pre-registration item 8 reads
two levels — the coarse ``labels.provider_era`` and the fine ``ServingWindow`` of provider,
runtime version and model — so a holdout that moved only the label would differ in the level
nothing reads and agree in the level everything does."""

SUITE_VERSION = "v1"
CONFIGURATION_VERSION = "router-baselines-v1"
PREREGISTRATION_SHA256 = "6b45998b3a9847298ba878a6414211ce38d64c775e5addd587509fdd70cf04ea"

ABLATIONS_PATH = "evals/router/ablations.v1.json"
"""Where protocol §14's registered ablation table lives, relative to the repository root.

Named by the config rather than found by convention for the same reason the oracle subset
and the v0.1 configuration table are: a run's ablations are part of what was pre-registered,
and a benchmark that located them by a hard-coded path could be pointed at a different table
by moving a file rather than by editing a reviewed line."""

NODE_CLASSES: tuple[str, ...] = ("IMPLEMENTATION", "ANALYSIS", "VERIFICATION", "MIGRATION")
STRATEGY_DECISIONS: tuple[str, ...] = ("DIRECT", "LOOP", "GRAPH", "HYBRID")

LATENCY_BUDGET_MS = 60_000
"""What ``latency_ms`` is normalised by before it enters utility. One minute per node."""

INVALID_ACTION_PENALTY = 1.0
"""One whole unit of quality. An invalid selection is worth less than the worst valid one."""

TRIALS_PER_CELL = 2
"""Protocol §9 wants repeated measurement; two is the minimum that has a spread at all."""

SELECTION_PROJECT_IDS: tuple[str, ...] = (
    "prj-router-cli-generator",
    "prj-router-cli-scaffold",
    "prj-router-infra-terraform",
    "prj-router-paper-baseline",
    "prj-router-paper-benchmark",
    "prj-router-paper-extension",
)
"""The half that may pick the fixed baseline. Whole lineages: the CLI family, the three-link
paper chain, and one standalone. No root is divided, so nothing here shares an ancestor,
a repository digest or a task family with anything in the evaluation half."""

EVALUATION_PROJECT_IDS: tuple[str, ...] = (
    "prj-router-docs-site",
    "prj-router-etl-batch",
    "prj-router-etl-batch-fork",
    "prj-router-notebook-analysis",
    "prj-router-web-api",
    "prj-router-web-api-fork",
)
"""The half every quoted number is measured on: two fork pairs and two standalones."""

# --------------------------------------------------------------------------------------
# The declared structure: six configurations, their eligibility, and their per-class means.
# --------------------------------------------------------------------------------------

CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "cnf-claude-opus-full",
        "provider": "CLAUDE",
        "runtime_id": "claude-cli",
        "runtime_version": "2.4.0",
        "model_id": "claude-opus-4",
        "tool_profile": "full",
        "declared_cost": 0.62,
        "declared_latency_ms": 41_000,
        "predicted_success": 0.86,
        "eligible_node_classes": ["ANALYSIS", "IMPLEMENTATION", "MIGRATION", "VERIFICATION"],
        "quality_by_node_class": {
            "IMPLEMENTATION": 0.78,
            "ANALYSIS": 0.74,
            "VERIFICATION": 0.62,
            "MIGRATION": 0.82,
        },
    },
    {
        "candidate_id": "cnf-claude-sonnet-lean",
        "provider": "CLAUDE",
        "runtime_id": "claude-cli",
        "runtime_version": "2.4.0",
        "model_id": "claude-sonnet-4",
        "tool_profile": "lean",
        "declared_cost": 0.28,
        "declared_latency_ms": 22_000,
        "predicted_success": 0.72,
        "eligible_node_classes": ["ANALYSIS", "IMPLEMENTATION", "VERIFICATION"],
        "quality_by_node_class": {
            "IMPLEMENTATION": 0.68,
            "ANALYSIS": 0.82,
            "VERIFICATION": 0.58,
        },
    },
    {
        "candidate_id": "cnf-codex-standard",
        "provider": "CODEX",
        "runtime_id": "codex-cli",
        "runtime_version": "1.9.0",
        "model_id": "codex-standard",
        "tool_profile": "standard",
        "declared_cost": 0.41,
        "declared_latency_ms": 28_000,
        "predicted_success": 0.78,
        "eligible_node_classes": ["IMPLEMENTATION", "MIGRATION", "VERIFICATION"],
        "quality_by_node_class": {
            "IMPLEMENTATION": 0.82,
            "VERIFICATION": 0.72,
            "MIGRATION": 0.70,
        },
    },
    {
        "candidate_id": "cnf-codex-mini",
        "provider": "CODEX",
        "runtime_id": "codex-cli",
        "runtime_version": "1.9.0",
        "model_id": "codex-mini",
        "tool_profile": "minimal",
        "declared_cost": 0.12,
        "declared_latency_ms": 15_000,
        "predicted_success": 0.58,
        "eligible_node_classes": ["ANALYSIS", "VERIFICATION"],
        "quality_by_node_class": {
            "ANALYSIS": 0.68,
            "VERIFICATION": 0.66,
        },
    },
    {
        "candidate_id": "cnf-opencode-local",
        "provider": "OPENCODE",
        "runtime_id": "opencode-cli",
        "runtime_version": "0.7.0",
        "model_id": "local-coder",
        "tool_profile": "local",
        "declared_cost": 0.05,
        "declared_latency_ms": 52_000,
        "predicted_success": 0.49,
        "eligible_node_classes": ["ANALYSIS"],
        "quality_by_node_class": {
            "ANALYSIS": 0.58,
        },
    },
    {
        "candidate_id": "cnf-deterministic-scripted",
        "provider": "DETERMINISTIC",
        "runtime_id": "deterministic-runtime",
        "runtime_version": "1.0.0",
        "model_id": "scripted-v1",
        "tool_profile": "scripted",
        "declared_cost": 0.02,
        "declared_latency_ms": 5_000,
        "predicted_success": 0.35,
        "eligible_node_classes": ["MIGRATION", "VERIFICATION"],
        "quality_by_node_class": {
            "VERIFICATION": 0.82,
            "MIGRATION": 0.54,
        },
    },
)

ORACLE_CANDIDATE_SUBSET: tuple[str, ...] = (
    "cnf-claude-opus-full",
    "cnf-claude-sonnet-lean",
    "cnf-codex-mini",
    "cnf-codex-standard",
    "cnf-deterministic-scripted",
)
"""Protocol §8.3's registered subset: the five configurations executed under matched
conditions. ``cnf-opencode-local`` is deliberately outside it — it is a real, admissible,
selectable configuration whose environment was never matched, so it may be *chosen* but may
not define the post-hoc bound. A subset that happened to be everything would make §8.3 a
sentence rather than a constraint."""

DETERMINISTIC_V01_TABLE: Mapping[str, str] = {
    "DIRECT": "cnf-codex-mini",
    "LOOP": "cnf-claude-sonnet-lean",
    "GRAPH": "cnf-codex-standard",
    "HYBRID": "cnf-claude-opus-full",
}
"""What the v0.1 selector's execution mode meant, as a configuration. Declared in the corpus
rather than in code so the M2 comparator can be re-pointed at another v0.1 deployment
without editing the policy."""

PROJECT_IDS: tuple[str, ...] = tuple(sorted(SELECTION_PROJECT_IDS + EVALUATION_PROJECT_IDS))

UTILITY_WEIGHTS: Mapping[str, float] = {"quality": 1.0, "cost": 0.30, "latency": 0.15}
VERIFIED_SUCCESS_FLOOR = 0.70
FALSE_ACCEPTANCE_CEILING = 0.05


def _round(value: float) -> float:
    """Six decimal places, the precedent ``benchmark.py`` sets for every emitted metric."""

    return round(value, 6)


def prefixed(project_id: str, prefix: str | None) -> str:
    """``prj-router-web-api`` under prefix ``locked`` is ``prj-router-locked-web-api``.

    A rename and nothing else. The lineage structure — which projects share a repository, which
    declares an ancestor — is carried by :func:`derived_projects`, so the two halves of a
    derived corpus rotate node classes at the same offsets the shipped corpus does and the
    halves stay the comparable populations the shipped design made them.
    """

    if prefix is None:
        return project_id
    return project_id.replace("prj-router-", f"prj-router-{prefix}-", 1)


def _derived_identity(repository_identity: str, prefix: str) -> str:
    """A fresh repository digest for a derived lineage: ``sha256("<prefix>:<identity>")``.

    Derived rather than reused, because the repository digest is an *edge* in
    :func:`~accretion.routing.split.lineage_roots`: a locked project that kept the shipped
    project's digest would be united with it into one lineage, and a corpus generated to be
    disjoint from the one everybody iterated on would silently share its ancestry.
    """

    return hashlib.sha256(f"{prefix}:{repository_identity}".encode()).hexdigest()


def _bumped(version: str, minor_bump: int) -> str:
    """``2.4.0`` bumped by one is ``2.5.0``; by zero it is itself.

    Only the minor component moves. A major bump would say the runtime changed
    incompatibly — a claim the corpus has no evidence for — and a patch bump would be a
    version window nothing distinguishes.
    """

    if minor_bump == 0:
        return version
    major, minor, patch = version.split(".")
    return f"{major}.{int(minor) + minor_bump}.{patch}"


def _candidates(runtime_minor_bump: int) -> tuple[dict[str, Any], ...]:
    """The six declared configurations, with their runtimes moved by ``runtime_minor_bump``.

    Everything else — eligibility, cost points, per-class quality means — is untouched, so a
    later-era corpus is the same designed world served by later runtimes and not a different
    experiment wearing the same configuration ids.
    """

    if runtime_minor_bump == 0:
        return CANDIDATES
    return tuple(
        {
            **candidate,
            "runtime_version": _bumped(str(candidate["runtime_version"]), runtime_minor_bump),
        }
        for candidate in CANDIDATES
    )


def derived_projects(prefix: str, provider_era: str | None = None) -> list[dict[str, Any]]:
    """The shipped registry re-keyed under ``prefix``, optionally forced to one provider era.

    Read from :data:`PROJECTS_PATH` in the shipped order rather than restated, so the derived
    registry has exactly the shipped lineage structure: the two fork pairs (one declaring no
    ancestry at all, so the repository digest is the only edge), the three-link paper chain and
    the standalones. ``provider_era`` set replaces every label, which is what makes the drift
    holdout a single-era population; left ``None`` the shipped mixture is kept.
    """

    document = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
    projects: list[dict[str, Any]] = []
    for project in document["projects"]:
        labels = dict(project["labels"])
        if provider_era is not None:
            labels["provider_era"] = provider_era
        projects.append(
            {
                "project_id": prefixed(str(project["project_id"]), prefix),
                "repository_identity": _derived_identity(
                    str(project["repository_identity"]), prefix
                ),
                "task_family": f"{prefix}-{project['task_family']}",
                "ancestors": [
                    prefixed(str(ancestor), prefix) for ancestor in project["ancestors"]
                ],
                "labels": labels,
            }
        )
    return projects


def _tasks(
    rng: random.Random,
    candidates: Sequence[Mapping[str, Any]],
    halves: Sequence[Sequence[str]],
) -> list[dict[str, Any]]:
    """Three nodes per project, each with a node class, a v0.1 decision and a planner choice.

    Node classes and strategy decisions are *rotated*, not sampled, and the rotation runs
    **within each half of the split**. Six projects taking three of four classes at a moving
    offset gives each half the same class histogram, so the selection half and the evaluation
    half are comparable populations and a configuration cannot win the baseline slot merely
    by being good at whichever class the selection half happened to draw more of. Rotating
    over all twelve projects instead would have left the two halves lopsided, and a seeded
    draw would have left the counts to luck; the counts are part of the design.

    The planner's recorded choice *is* sampled, but its failure rate is not: about one task
    in seven draws from the configurations the node **refuses**, and the rest draw from the
    admissible ones. Sampling uniformly from all six and hoping some of them landed outside
    the admissible set would have made the corpus's coverage of the invalid-action path a
    property of the seed, and on the first seed tried it produced none at all.
    """

    tasks: list[dict[str, Any]] = []
    position = 0
    for half in halves:
        for offset, project_id in enumerate(half):
            classes = [NODE_CLASSES[(offset + step) % len(NODE_CLASSES)] for step in range(3)]
            for index, node_class in enumerate(classes):
                position += 1
                admissible = [
                    candidate["candidate_id"]
                    for candidate in candidates
                    if node_class in candidate["eligible_node_classes"]
                ]
                refused = [
                    candidate["candidate_id"]
                    for candidate in candidates
                    if node_class not in candidate["eligible_node_classes"]
                ]
                pool = refused if refused and rng.random() < 0.15 else admissible
                health = index != 2
                tasks.append(
                    {
                        "task_id": f"{project_id}-n{index}",
                        "project_id": project_id,
                        "run_id": f"run-{project_id}",
                        "node_class": node_class,
                        "strategy_decision": STRATEGY_DECISIONS[position % len(STRATEGY_DECISIONS)],
                        "planner_choice": rng.choice(sorted(pool)),
                        "predicted_success": {
                            candidate["candidate_id"]: _round(
                                min(
                                    0.99,
                                    max(
                                        0.01,
                                        float(candidate["predicted_success"])
                                        + rng.uniform(-0.12, 0.12),
                                    ),
                                )
                            )
                            for candidate in candidates
                        },
                        "performance_scores": {
                            candidate["candidate_id"]: _round(rng.uniform(0.2, 0.95))
                            for candidate in candidates
                        },
                        "historical_quality": {
                            f"{candidate['provider']}|{candidate['runtime_version']}": _round(
                                rng.uniform(0.35, 0.9)
                            )
                            for candidate in candidates
                        },
                        "runtime_health": _runtime_health(rng, candidates) if health else [],
                    }
                )
    return tasks


def _runtime_health(
    rng: random.Random, candidates: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """One health row per distinct runtime, so M3 can run the real v0.2 router.

    Tasks without these rows are the ones recorded before health telemetry existed, and they
    are what sends :class:`~accretion.routing.baselines.PerformanceAwarePolicy` down its
    declared-score path. Both branches need coverage, so a third of the corpus has no health.
    """

    seen: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        runtime_id = str(candidate["runtime_id"])
        if runtime_id in seen:
            continue
        seen[runtime_id] = {
            "runtime_id": runtime_id,
            "provider": candidate["provider"],
            "status": rng.choice(["READY", "READY", "BUSY", "DEGRADED"]),
            "auth_mode": "SUBSCRIPTION",
            "runtime_version": candidate["runtime_version"],
            "observed_usage_pressure": rng.choice(["LOW", "LOW", "MEDIUM", "HIGH"]),
            "observed_at": "2026-09-01T00:00:00Z",
        }
    return [seen[runtime_id] for runtime_id in sorted(seen)]


def _traces(
    rng: random.Random,
    tasks: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    trials_per_cell: int,
) -> list[dict[str, Any]]:
    """Every task × configuration × trial cell, admissible or not.

    The grid is **complete** on purpose. The estimands refuse partial coverage — a
    configuration missing a task would make the oracle an oracle over a different task set —
    and protocol §8.3's post-hoc bound needs every registered configuration to have been run
    on every task. Cells whose configuration does not serve the node class are recorded as
    refusals: zero quality, no verification, ``invalid`` set. They are what an invalid
    selection costs, and leaving them out would make an invalid selection cost nothing.
    """

    traces: list[dict[str, Any]] = []
    for task in tasks:
        node_class = str(task["node_class"])
        for candidate in candidates:
            serves = node_class in candidate["eligible_node_classes"]
            base = float(candidate["quality_by_node_class"].get(node_class, 0.0))
            for trial in range(trials_per_cell):
                if not serves:
                    traces.append(
                        {
                            "task_id": task["task_id"],
                            "candidate_id": candidate["candidate_id"],
                            "trial": trial,
                            "quality": 0.0,
                            "cost": _round(float(candidate["declared_cost"]) * 0.25),
                            "latency_ms": 2_000,
                            "verified": False,
                            "false_accept": False,
                            "invalid": True,
                        }
                    )
                    continue
                quality = min(0.99, max(0.0, base + rng.uniform(-0.14, 0.14)))
                cost = min(1.0, float(candidate["declared_cost"]) * rng.uniform(0.85, 1.2))
                latency_ms = int(float(candidate["declared_latency_ms"]) * rng.uniform(0.8, 1.3))
                verified = quality >= 0.70
                traces.append(
                    {
                        "task_id": task["task_id"],
                        "candidate_id": candidate["candidate_id"],
                        "trial": trial,
                        "quality": _round(quality),
                        "cost": _round(cost),
                        "latency_ms": latency_ms,
                        "verified": verified,
                        "false_accept": bool(verified and rng.random() < 0.06),
                        "invalid": False,
                    }
                )
    return traces


def build(
    *,
    seed: int | None = None,
    trials_per_cell: int | None = None,
    project_prefix: str | None = None,
    provider_era: str | None = None,
    runtime_minor_bump: int = 0,
) -> dict[str, dict[str, Any]]:
    """The corpus documents, as a mapping from file stem to document.

    One :class:`random.Random` threaded through the whole build, in a fixed order: tasks
    first, then traces. Two generators, or a different order, would produce a different
    corpus from the same seed, which is why the order is stated here rather than left to the
    call sites.

    ``seed`` and ``trials_per_cell`` default to ``None`` and resolve to the module constants
    :data:`SEED` and :data:`TRIALS_PER_CELL` **at call time**, not at definition time. That is
    the difference between a parameter and a broken monkeypatch: ``scripts/router_pilot.py``
    rebinds ``TRIALS_PER_CELL`` around a build to regenerate the pilot corpus at nine trials,
    and a default captured when this function was defined would silently ignore it.

    ``project_prefix`` renames every project (and therefore every task and run id) and makes
    the build emit a fifth document, ``projects.v1``: a corpus under a new prefix names
    lineages the hand-authored development registry has never heard of, and
    :meth:`~accretion.router_benchmark.RouterBenchmarkCorpus.load` proves the split is clean
    against the registry beside the corpus. Without a prefix nothing is emitted, because the
    shipped corpus's registry is hand-authored and is not this function's to rewrite.
    """

    seed = SEED if seed is None else seed
    trials_per_cell = TRIALS_PER_CELL if trials_per_cell is None else trials_per_cell
    candidates = _candidates(runtime_minor_bump)
    selection_ids = [prefixed(project_id, project_prefix) for project_id in SELECTION_PROJECT_IDS]
    evaluation_ids = [
        prefixed(project_id, project_prefix) for project_id in EVALUATION_PROJECT_IDS
    ]
    rng = random.Random(seed)
    tasks = _tasks(rng, candidates, (selection_ids, evaluation_ids))
    traces = _traces(rng, tasks, candidates, trials_per_cell)
    config = {
        "suite_version": SUITE_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "seed": seed,
        "invalid_action_penalty": INVALID_ACTION_PENALTY,
        "latency_budget_ms": LATENCY_BUDGET_MS,
        "weights": dict(UTILITY_WEIGHTS),
        "verified_success_floor": VERIFIED_SUCCESS_FLOOR,
        "false_acceptance_ceiling": FALSE_ACCEPTANCE_CEILING,
        "selection_split": {
            "selection_project_ids": selection_ids,
            "evaluation_project_ids": evaluation_ids,
        },
        "oracle_candidate_subset": list(ORACLE_CANDIDATE_SUBSET),
        "deterministic_v01_table": dict(DETERMINISTIC_V01_TABLE),
        "ablations_path": ABLATIONS_PATH,
        "preregistration_sha256": PREREGISTRATION_SHA256,
    }
    candidates_document = {
        "suite_version": SUITE_VERSION,
        "candidates": [
            {key: value for key, value in candidate.items() if key != "quality_by_node_class"}
            for candidate in candidates
        ],
    }
    documents: dict[str, dict[str, Any]] = {
        "config.v1": config,
        "tasks.v1": {"suite_version": SUITE_VERSION, "tasks": tasks},
        "candidates.v1": candidates_document,
        "replay-traces.v1": {"suite_version": SUITE_VERSION, "traces": traces},
    }
    if project_prefix is not None:
        documents["projects.v1"] = {
            "suite_version": SUITE_VERSION,
            "projects": derived_projects(project_prefix, provider_era),
        }
    return documents


def write(root: Path = CORPUS_ROOT, **options: Any) -> list[Path]:
    """Write the documents, sorted-key and two-space indented, and return their paths.

    The same formatting the contract-schema exporter uses, and for the same reason: the files
    are committed, so regeneration must produce a byte-identical diff or the corpus becomes
    unreviewable noise. ``options`` are :func:`build`'s keyword parameters, passed straight
    through so that there is one place a corpus's shape is decided.
    """

    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stem, document in build(**options).items():
        path = root / f"{stem}.json"
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


LOCKED_OPTIONS: Mapping[str, Any] = {
    "seed": LOCKED_SEED,
    "trials_per_cell": FROZEN_TRIALS_PER_CELL,
    "project_prefix": "locked",
}
"""Exactly what makes the locked corpus: a new seed, the frozen size, a new set of names.

Declared as data and not as a call site so that the corpus test and the writer below cannot
disagree about what "the locked corpus" means — a test that rebuilt it from its own literals
would be checking the generator against a copy of the generator."""

DRIFT_OPTIONS: Mapping[str, Any] = {
    "seed": DRIFT_SEED,
    "trials_per_cell": FROZEN_TRIALS_PER_CELL,
    "project_prefix": "drift",
    "provider_era": DRIFT_PROVIDER_ERA,
    "runtime_minor_bump": DRIFT_RUNTIME_MINOR_BUMP,
}
"""The holdout: the locked corpus's size at a third seed, one provider era, later runtimes."""


def write_locked(root: Path = LOCKED_ROOT) -> list[Path]:
    """Write ``evals/router/locked``: the corpus every published number is measured on."""

    return write(root, **LOCKED_OPTIONS)


def write_drift(root: Path = DRIFT_ROOT) -> list[Path]:
    """Write ``evals/router/drift``: the same world one provider era later."""

    return write(root, **DRIFT_OPTIONS)


if __name__ == "__main__":  # pragma: no cover - a maintenance entry point, not a test path
    for written_path in [*write(), *write_locked(), *write_drift()]:
        print(written_path)
