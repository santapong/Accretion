"""Protocol §14's ten required ablations: registered, distinct, and each one a run.

A required ablation that exists only in a table is a claim about work nobody did. The first
test here is the whole of the §14 obligation in one parametrisation: for every id A1 through
A10 there is a registered entry naming a component and a question, it resolves to a flag
configuration that differs from the full router in exactly that one component, and the router
benchmark *runs* under it and returns a row for every evaluation task. Deleting an entry from
``evals/router/ablations.v1.json`` fails it; making :func:`from_ablation` ignore which id it
was given fails it, because the ten configurations would stop being ten.

**The registry is checked as a bijection, not as a count.** Ten entries clearing nine
components — one of them twice — is ten rows in a report, ten runs on a chart, and one §14
question that was never asked. :func:`load_ablations` refuses that shape and two tests below
build exactly it, because it is the failure that survives every count-based check.

**A registered ablation that changes nothing is a decorative ablation.** "It runs and returns
rows" is the §14 obligation and it is *not* evidence that the component was removed: a flag
read that had been quietly turned into a no-op would still run and still return rows, and the
table would go on reporting ten measurements of one router. So every one of the ten has a
second test that runs the same comparator twice over the same corpus, under ``FULL`` and
under its own configuration, and pins a divergence to that component and to no other:

* **A1, A2** at the slate — a duplicate configuration survives, a refused one reaches the list.
* **A3** on the receipt, and in the two history columns a fit is denied.
* **A4** — M8 under A4 scores *identically to M7*, to the last digit, while the full M8 does
  not: the local correction is the whole of the difference between those two comparators.
* **A5, A6** — the corpus contains nodes that verified inside runs that did not, so the node
  verdict and the run verdict genuinely disagree, and each ablation swaps one head's label for
  the other's. A6 is pinned twice over: the node head comes out bit-identical and only the
  run term moves, which is what makes it a measurement of the *global* signal alone.
* **A7** — asserted on the action and not on the propensity. A propensity below one only says
  the distribution had mass elsewhere; it is satisfied by a "draw" that always returns the
  greedy arm. What is asserted is that M9 under ``FULL`` really selects something M9 under A7
  does not, and that M9 under A7 is exactly M8's argmax on every task.
* **A8** — a false acceptance is a verification pass, so the full router labels those cells a
  failure and the ablation labels them a success; on a corpus whose fitting half is all false
  acceptances, that reaches the fitted model and moves the choices.
* **A9** — the ranked estimate is the conformal lower bound under ``FULL`` and the calibrated
  mean under A9, from one identical fit, and the success floor removes a candidate the
  ablated ranker then chooses.
* **A10** — the stop rule reads a cell that verified on its first trial as one paid trial, and
  the ablation reads the pooled two: a different verdict, and a strictly higher predicted cost.

**Nothing learned reads the evaluation half.** The last test edits every evaluation-half
trace in a copied corpus and requires the learned comparators' *selection-half* choices to be
unchanged. A fit that had touched the evaluation rows would move, and no other test in the
suite would notice, because a leaked fit does not look wrong — it looks good.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType

import pytest
from test_v04_m2_candidates import PRINCIPAL, build, task, world

from accretion.contracts import BenchmarkExecutionSource, Provider
from accretion.governance import CapabilityPolicyEngine
from accretion.persistence.store import MemoryStore
from accretion.router_benchmark import (
    BenchmarkSplit,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
)
from accretion.routing.baselines import (
    CALIBRATION_PROJECTS,
    AdaptedRankerPolicy,
    BenchmarkCandidate,
    BenchmarkContext,
    LearnedRankerPolicy,
    ReplayEvidence,
    _cell,
    _head_label,
    _label_table,
    _run_head_label,
    _Scored,
)
from accretion.routing.candidates import CandidateBuilder
from accretion.routing.compatibility import CompatibilityEngine
from accretion.routing.flags import (
    ABLATION_LABEL,
    ABLATIONS_PATH,
    FLAG_NAMES,
    FULL,
    AblationRegistryError,
    RouterFeatureFlags,
    UnknownAblation,
    from_ablation,
    load_ablations,
)
from accretion.routing.gates import PolicyGate
from accretion.routing.protocols import RoutingMode
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.stages import BehaviorDecision, DeterministicBehavior

ABLATION_IDS: tuple[str, ...] = (
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "A6",
    "A7",
    "A8",
    "A9",
    "A10",
)
"""Protocol §14's ten ids, written out rather than read from the file under test.

Reading them from the registry would make every assertion below self-referential: deleting
A6 would delete it from the expectation too and the parametrisation would pass with nine."""

CORPUS_FILES = (
    "config.v1.json",
    "tasks.v1.json",
    "candidates.v1.json",
    "replay-traces.v1.json",
    "projects.v1.json",
    "ablations.v1.json",
)


def setup_registry_copy(tmp_path: Path, mutate: object) -> Path:
    """The shipped §14 registry, copied somewhere a test may break it deliberately."""

    path = tmp_path / "ablations.v1.json"
    document = json.loads(ABLATIONS_PATH.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def setup_corpus_copy(tmp_path: Path) -> Path:
    """The shipped corpus, copied somewhere a test may edit its traces."""

    root = tmp_path / "router"
    root.mkdir(parents=True)
    shipped = RouterBenchmarkCorpus.load().root
    for name in CORPUS_FILES:
        shutil.copy(shipped / name, root / name)
    return root


def rewrite(path: Path, mutate: object) -> None:
    """Apply ``mutate`` to a corpus document and write it back with the shipped formatting."""

    document = json.loads(path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def setup_contexts(
    runner: RouterBenchmarkRunner, split: BenchmarkSplit = BenchmarkSplit.EVALUATION
) -> tuple[BenchmarkContext, ...]:
    """One decision-time context per task on ``split``, in the corpus's own order.

    Built here rather than taken from the runner because the differential tests below need
    the *scores* a comparator computed and not only the choice it made, and a score is a
    property of a policy applied to a context. Only the fields the learned comparators read
    are populated — a context carrying the fixed-baseline grid or the oracle's subset would
    say those fields matter to M7, and they do not.
    """

    reported = set(runner.corpus.task_ids_for(split))
    return tuple(
        BenchmarkContext(
            task_id=task_row.task_id,
            project_id=task_row.project_id,
            run_id=task_row.run_id,
            node_class=task_row.node_class,
            execution_source=BenchmarkExecutionSource.REPLAY,
            strategy_decision=task_row.strategy_decision,
            planner_choice=task_row.planner_choice,
            predicted_success=MappingProxyType(dict(task_row.predicted_success)),
            performance_scores=MappingProxyType(dict(task_row.performance_scores)),
        )
        for task_row in runner.corpus.tasks
        if task_row.task_id in reported
    )


def setup_ranker(
    evidence: ReplayEvidence, ablation_id: str | None, *, adapted: bool = False
) -> LearnedRankerPolicy:
    """M7 (or M8) under one §14 configuration, on the evidence a caller already holds.

    Both configurations of a pair must be handed the *same* evidence object, or the
    comparison is between two corpora rather than between two routers.
    """

    flags = FULL if ablation_id is None else from_ablation(ablation_id)
    if adapted:
        return AdaptedRankerPolicy(flags=flags, evidence=evidence)
    return LearnedRankerPolicy(flags=flags, evidence=evidence)


def scores_of(
    policy: LearnedRankerPolicy,
    context: BenchmarkContext,
    candidates: Sequence[BenchmarkCandidate],
) -> dict[str, float]:
    """What one policy scored every candidate at, keyed by candidate id."""

    return {item.candidate.candidate_id: item.score for item in policy.scored(context, candidates)}


def choices_that_moved(
    runner: RouterBenchmarkRunner, policy_id: str, ablation_id: str
) -> list[str]:
    """The tasks one comparator decides differently once one component is removed.

    The whole differential claim in one call, and deliberately a *list of task ids* rather
    than a count: a test that fails says which decisions moved, and a test that fails because
    none did says the ablation is inert.
    """

    chosen = runner.selections_for(policy_id)
    without = runner.selections_for(policy_id, flags=from_ablation(ablation_id))
    return sorted(
        task_id
        for task_id in chosen
        if chosen[task_id].candidate_id != without[task_id].candidate_id
    )


def fitting_task_ids(runner: RouterBenchmarkRunner) -> frozenset[str]:
    """The selection-half tasks a fit is taken over, calibration projects excluded.

    Derived from the corpus and :data:`CALIBRATION_PROJECTS` rather than hard-coded, so a
    test that needs a disagreement *inside the fit* keeps needing one after a re-split.
    """

    selection = set(runner.corpus.task_ids_for(BenchmarkSplit.SELECTION))
    projects = sorted(
        {row.project_id for row in runner.corpus.tasks if row.task_id in selection}
    )
    fitting = set(projects[:-CALIBRATION_PROJECTS])
    return frozenset(row.task_id for row in runner.corpus.tasks if row.project_id in fitting)


def scored_stub(
    candidate_id: str, *, lower_confidence_success: float, score: float
) -> _Scored:
    """One already-estimated candidate, for the gate that no shipped corpus row exercises.

    The success floor is a filter on the conformal lower bound, and on the committed corpus
    the bound is either above the floor for every eligible configuration on a task or below
    it for all of them — so the shipped rows never make the filter *choose*. Handing
    :meth:`LearnedRankerPolicy.ranked` a slate that does is the only way to show that A9
    removes the floor and not merely the conservative estimate; the alternative is a test
    that passes because the gate was never reached.
    """

    return _Scored(
        candidate=BenchmarkCandidate(
            candidate_id=candidate_id,
            provider=Provider.CLAUDE,
            runtime_id="rt-stub",
            runtime_version="1.0.0",
            model_id="mdl-stub",
            tool_profile="tp-stub",
            declared_cost=0.2,
            declared_latency_ms=1000,
            predicted_success=0.5,
            eligible_node_classes=frozenset({"IMPLEMENTATION"}),
        ),
        hard_eligible=True,
        mean_success=min(1.0, lower_confidence_success + 0.2),
        lower_confidence_success=lower_confidence_success,
        adapted_success=lower_confidence_success,
        run_success=lower_confidence_success,
        predicted_utility=score,
        score=score,
        cost_ucb=0.3,
        cost_lcb=0.1,
        prior_logit=0.0,
    )


class UnusedSnapshots:
    """The snapshot builder a constructor is not allowed to consult.

    :class:`~accretion.routing.snapshot.RegistrySnapshotBuilder` needs a resolver, the runtime
    adapters and a verifier registry, and building all four to assert that a constructor
    stored a flag would be building a world to test an assignment. This stands in its place
    and fails loudly if anything ever calls it, which is the assertion these tests actually
    want: nothing routes here.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def build(self, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("a routing service constructor must not read the registry")


class CountingBehavior:
    """A behaviour policy that records how often it was asked, and is never asked here.

    Hand-written rather than mocked for the reason this repository always gives: the object
    under test is the *constructor*, and a mock would answer "was this stored" by construction
    while this answers it by being the object the service kept.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def select(self, **kwargs: object) -> BehaviorDecision:
        self.calls += 1
        raise AssertionError("the behaviour policy must not be consulted by a constructor")


def _unused_catalog(**kwargs: object) -> object:
    raise AssertionError("a routing service constructor must not build a catalog")


def setup_service(flags: RouterFeatureFlags) -> tuple[DefaultNodeRoutingService, CountingBehavior]:
    """A routing service built with one flag configuration and a behaviour policy to watch.

    Nothing routes here. The claim is about what the constructor assembles, which is where
    A7 is enforced: a bandit that were merely skipped at the call site would still be held.
    """

    store = MemoryStore()
    behavior = CountingBehavior()
    snapshots = UnusedSnapshots()
    service = DefaultNodeRoutingService(
        store=store,
        snapshots=snapshots,  # type: ignore[arg-type]
        catalog_factory=_unused_catalog,  # type: ignore[arg-type]
        runtimes={},
        behavior=behavior,  # type: ignore[arg-type]
        default_mode=RoutingMode.BASELINE_ONLY,
        flags=flags,
    )
    assert snapshots.calls == 0
    return service, behavior


# --------------------------------------------------------------------------------------
# The registry.
# --------------------------------------------------------------------------------------


def test_the_registered_table_is_the_protocols_own_ten_rows() -> None:
    registered = load_ablations()

    assert list(registered) == list(ABLATION_IDS)
    cleared = [entry.cleared_flag for entry in registered.values()]
    assert sorted(cleared) == sorted(FLAG_NAMES), "one ablation per component, no component twice"
    for ablation_id, entry in registered.items():
        assert entry.ablation_id == ablation_id
        assert entry.removed_component, "an ablation must name what it removes"
        assert entry.question.endswith("?"), "§14's third column is a question"


def test_full_leaves_every_component_in_place() -> None:
    assert FULL == RouterFeatureFlags()
    assert all(FULL.enabled(flag) for flag in FLAG_NAMES)
    assert FULL.removed == ()
    assert FULL.ablation_id is None
    # The property every default in the codebase rests on: an unablated router says nothing
    # about ablations on its receipts, so no production decision can acquire the provenance
    # of an experiment.
    assert FULL.labels() == {}


def test_each_ablation_removes_exactly_one_component_and_no_two_remove_the_same_one() -> None:
    removed = {ablation_id: from_ablation(ablation_id).removed for ablation_id in ABLATION_IDS}

    for ablation_id, flags in removed.items():
        assert len(flags) == 1, f"{ablation_id} must remove exactly one component"
        assert from_ablation(ablation_id).ablation_id == ablation_id
    assert len({flags for flags in removed.values()}) == len(ABLATION_IDS)
    assert sorted(flag for flags in removed.values() for flag in flags) == sorted(FLAG_NAMES)


def test_an_unknown_ablation_id_is_refused() -> None:
    with pytest.raises(UnknownAblation) as refusal:
        from_ablation("A11")
    assert "A11" in str(refusal.value)
    assert "A1" in str(refusal.value), "the refusal names what is registered"


def test_a_registry_that_lost_an_ablation_is_refused(tmp_path: Path) -> None:
    """The mutation the parametrised run exists to catch, made directly and at the door."""

    def drop_a6(document: dict[str, object]) -> None:
        entries = document["ablations"]
        assert isinstance(entries, list)
        document["ablations"] = [
            entry for entry in entries if entry["ablation_id"] != "A6"
        ]

    path = setup_registry_copy(tmp_path, drop_a6)
    with pytest.raises(AblationRegistryError) as refusal:
        load_ablations(path)
    assert "final_run_feedback" in str(refusal.value)


def test_a_registry_that_ablates_one_component_twice_is_refused(tmp_path: Path) -> None:
    """Ten entries, nine components: the shape a count-based check would call complete."""

    def double_up(document: dict[str, object]) -> None:
        entries = document["ablations"]
        assert isinstance(entries, list)
        for entry in entries:
            if entry["ablation_id"] == "A6":
                entry["cleared_flag"] = "node_feedback"

    path = setup_registry_copy(tmp_path, double_up)
    with pytest.raises(AblationRegistryError) as refusal:
        load_ablations(path)
    assert "node_feedback" in str(refusal.value)
    assert len(json.loads(path.read_text(encoding="utf-8"))["ablations"]) == 10


# --------------------------------------------------------------------------------------
# Every registered ablation is a run.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M10-049")
@pytest.mark.parametrize("ablation_id", ABLATION_IDS)
def test_every_protocol_ablation_has_a_registered_entry_that_runs(ablation_id: str) -> None:
    runner = RouterBenchmarkRunner()
    table = runner.corpus.ablations_path
    assert table is not None, "the shipped corpus must register the §14 table"
    registered = load_ablations(table)
    assert ablation_id in registered, "protocol §14 requires this ablation to exist"
    entry = registered[ablation_id]

    # Reached through the corpus's own config line, never by a path the code knows.
    flags = runner.ablation(ablation_id)
    assert flags == from_ablation(ablation_id, path=table)
    assert flags.removed == (entry.cleared_flag,)
    assert not flags.enabled(entry.cleared_flag)

    result = runner.run(["M9"], flags=flags)
    policy = result.policy("M9")

    assert result.ablation == ablation_id
    assert policy.available, f"{ablation_id} must run, not be reported as unavailable"
    assert len(policy.rows) == len(result.reported_task_ids)
    assert policy.regret is not None and policy.gates is not None
    assert {row.ablation for row in policy.rows} == {ablation_id}
    # The full router is the comparison, so the ablated run must be scored over the same
    # tasks. An ablation measured on a different task set is not a comparison.
    assert sorted(row.task_id for row in policy.rows) == sorted(result.reported_task_ids)


def test_the_table_is_the_one_the_corpus_names_and_not_the_one_the_code_knows(
    tmp_path: Path,
) -> None:
    """Repointing ``config.v1.json``'s ``ablations_path`` changes which ablations run.

    The mutation the reviewer named: a loader that found the table by convention would keep
    answering from the shipped file no matter what the reviewed config line says, and this
    test pins that the config line is the authority — an alternative table in which ``A1``
    clears the uncertainty gate resolves ``A1`` to the uncertainty gate.
    """

    root = setup_corpus_copy(tmp_path)
    shipped = RouterBenchmarkRunner().corpus.ablations_path
    assert shipped is not None
    table = json.loads(shipped.read_text(encoding="utf-8"))
    entries = table["ablations"]
    a1 = next(entry for entry in entries if entry["ablation_id"] == "A1")
    a9 = next(entry for entry in entries if entry["ablation_id"] == "A9")
    a1["cleared_flag"], a9["cleared_flag"] = a9["cleared_flag"], a1["cleared_flag"]
    alternative = tmp_path / "swapped-ablations.v1.json"
    alternative.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def repoint(document: dict[str, object]) -> None:
        document["ablations_path"] = str(alternative)

    rewrite(root / "config.v1.json", repoint)
    repointed = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root))
    assert repointed.corpus.ablations_path == alternative
    assert repointed.ablation("A1").removed == ("uncertainty_gate",)
    assert repointed.ablation("A9").removed == ("hierarchical_construction",)
    # The shipped corpus still answers from the shipped table: nothing global moved.
    assert RouterBenchmarkRunner().ablation("A1").removed == ("hierarchical_construction",)


def test_a_corpus_that_names_no_table_cannot_be_ablated_against(tmp_path: Path) -> None:
    root = setup_corpus_copy(tmp_path)

    def forget(document: dict[str, object]) -> None:
        document.pop("ablations_path")

    rewrite(root / "config.v1.json", forget)
    runner = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root))
    assert runner.corpus.ablations_path is None
    with pytest.raises(AblationRegistryError, match="registers no ablation table"):
        runner.ablation("A1")

    def mislay(document: dict[str, object]) -> None:
        document["ablations_path"] = "evals/router/no-such-table.v1.json"

    rewrite(root / "config.v1.json", mislay)
    mislaid = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root))
    with pytest.raises(AblationRegistryError, match="does not exist"):
        mislaid.ablation("A1")


def test_the_full_router_is_the_run_every_ablation_is_compared_against() -> None:
    result = RouterBenchmarkRunner().run(["M9"])

    assert result.ablation is None
    assert {row.ablation for row in result.policy("M9").rows} == {None}


# --------------------------------------------------------------------------------------
# What the flags actually switch off.
# --------------------------------------------------------------------------------------


def test_the_guarded_router_takes_a_non_greedy_action_only_while_exploration_is_on() -> None:
    """A7, asserted on the action taken and never on the propensity of taking it.

    A propensity below one is not evidence that a draw happened. The number recorded is the
    probability mass of *whichever* candidate was chosen, and the greedy candidate's own mass
    is one minus everybody else's — below one whenever the distribution has any mass
    elsewhere, whether or not a uniform ever landed there. A ``_draw`` replaced by an argmax
    over the same distribution keeps every propensity exactly where it is and explores
    nothing, and a test that read only the propensity column would report guarded exploration
    as working while M9 selected M8's choice on all eighteen tasks.

    So the claim is made about the chosen candidate. M9 with A7 clear is M8's deterministic
    argmax, task for task — the assertion below is what identifies "greedy" — and the full
    M9 must really select something else somewhere. The propensity arithmetic §9.5's
    estimators consume is then checked *on those tasks*, which is where it means something.
    """

    runner = RouterBenchmarkRunner()

    result = runner.run(["M8", "M9"])
    explored = result.policy("M9")
    argmax = result.policy("M8")
    refused = runner.run(["M9"], flags=from_ablation("A7")).policy("M9")

    chosen = {row.task_id: row.selected_candidate_id for row in explored.rows}
    greedy = {row.task_id: row.selected_candidate_id for row in refused.rows}
    assert greedy == {
        row.task_id: row.selected_candidate_id for row in argmax.rows
    }, "with exploration removed M9 is its own greedy choice, which is M8's"

    drawn = sorted(task_id for task_id in chosen if chosen[task_id] != greedy[task_id])
    assert drawn, "the full router must actually take a non-greedy action, or A7 measures nothing"

    propensity = {row.task_id: row.propensity for row in explored.rows}
    assert all(0.0 < (propensity[task_id] or 1.0) < 1.0 for task_id in drawn), (
        "an action reached by a draw carries the probability the behaviour policy gave it"
    )
    assert all(row.propensity == 1.0 for row in refused.rows)
    # Removing exploration does not remove the router: A7 still selects on every task.
    assert len(refused.rows) == len(explored.rows)


def test_removing_hierarchical_construction_keeps_behaviourally_equivalent_duplicates() -> None:
    """§14 A1 at the layer that builds the slate, not the layer that ranks it."""

    node, snapshot, catalog, builder = world(models=("fake-model", "fake-model"))
    flat = CandidateBuilder(
        gate=PolicyGate(CapabilityPolicyEngine(set()), snapshot.policy, created_by=PRINCIPAL),
        evaluator=CompatibilityEngine(created_by=PRINCIPAL),
        catalog=catalog,
        created_by=PRINCIPAL,
        flags=from_ablation("A1"),
    )

    collapsed = build(builder, node, snapshot, task())
    duplicated = build(flat, node, snapshot, task())

    assert len(collapsed.candidates) == 1
    assert len(duplicated.candidates) > len(collapsed.candidates)
    hashes = {item.configuration.configuration_hash for item in duplicated.candidates}
    assert len(hashes) == 1, "the duplicates really are the same configuration twice"


def test_removing_compatibility_pruning_lets_a_refused_configuration_reach_the_slate() -> None:
    """§14 A2. The rule still runs and is still recorded; only the pruning is gone."""

    node, snapshot, catalog, builder = world()
    unpruned = CandidateBuilder(
        gate=PolicyGate(CapabilityPolicyEngine(set()), snapshot.policy, created_by=PRINCIPAL),
        evaluator=CompatibilityEngine(created_by=PRINCIPAL),
        catalog=catalog,
        created_by=PRINCIPAL,
        flags=from_ablation("A2"),
    )

    pruned = build(builder, node, snapshot, task(allowed=()))
    kept = build(unpruned, node, snapshot, task(allowed=()))

    assert pruned.candidates == ()
    assert pruned.rejected[0].reason_code == "CAPABILITY_NOT_ALLOWED"
    assert kept.candidates, "with pruning removed the refused configuration is still a candidate"
    assert all(not item.hard_eligible for item in kept.candidates)
    # The audit trail is not what A2 removes: the refusal was still evaluated and recorded.
    assert len(kept.compatibility_decisions) == len(pruned.compatibility_decisions)


# --------------------------------------------------------------------------------------
# The replay-side switches, each pinned to its own component.
#
# "It ran and returned rows" is the §14 obligation and not evidence that anything was
# removed: a flag read turned into a no-op still runs and still returns rows. Each test
# below runs one comparator twice over one corpus and shows a divergence that only its own
# component can produce.
# --------------------------------------------------------------------------------------


def test_removing_experience_retrieval_denies_the_fit_its_two_history_columns() -> None:
    """§14 A3 at the layer that learns, not only at the receipt that records it."""

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    contexts = setup_contexts(runner)
    full = setup_ranker(evidence, None)
    ablated = setup_ranker(evidence, "A3")

    moved = [
        context.task_id
        for context in contexts
        if scores_of(full, context, evidence.candidates)
        != scores_of(ablated, context, evidence.candidates)
    ]
    assert moved, "a router denied its retrieved history must score differently"

    assert choices_that_moved(
        runner, "M7", "A3"
    ), "and must choose differently somewhere, or A3 is a column nobody reads"


def test_removing_the_project_adapter_makes_m8_score_exactly_what_m7_scores() -> None:
    """§14 A4. The local correction is the whole of the difference between M8 and M7.

    Asserted as an equality and not as "both produced a selection". M8 under A4 must be M7 to
    the last digit on a project whose log is *not* empty — that is what says the adapter was
    skipped rather than fitted on nothing — while the full M8, holding the same log, must
    score differently. An adapter accidentally left in under A4 breaks the first assertion; an
    adapter that never does anything under ``FULL`` breaks the second.
    """

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    contexts = setup_contexts(runner)
    full = setup_ranker(evidence, None, adapted=True)
    ablated = setup_ranker(evidence, "A4", adapted=True)
    ranker = setup_ranker(evidence, None)

    # Every task but the last, so that both policies reach the last one holding a project log
    # of real decisions. With an empty log the adapter is skipped anyway and the comparison
    # would be vacuous.
    for context in contexts[:-1]:
        full.select(context, evidence.candidates)
        ablated.select(context, evidence.candidates)
    last = contexts[-1]

    assert scores_of(ablated, last, evidence.candidates) == scores_of(
        ranker, last, evidence.candidates
    ), "with A4 clear M8 is M7 exactly, log or no log"
    assert scores_of(full, last, evidence.candidates) != scores_of(
        ablated, last, evidence.candidates
    ), "and the full M8 corrected its prior from that project's own history"

    assert choices_that_moved(
        runner, "M8", "A4"
    ), "the correction has to change a decision somewhere, or it corrects nothing"


def test_removing_node_feedback_fits_the_node_head_on_the_runs_verdict_instead() -> None:
    """§14 A5, on the cells where the two verdicts genuinely disagree."""

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    labels = _label_table(evidence, FULL)
    fitting = fitting_task_ids(runner)
    disagreeing = [
        key
        for key in sorted(labels.node)
        if key[0] in fitting and labels.node[key] != labels.run[key]
    ]
    assert disagreeing, "A5 needs a node that verified inside a run that did not, inside the fit"
    key = disagreeing[0]
    assert (labels.node[key], labels.run[key]) == (1, 0)

    ablation = from_ablation("A5")
    assert _head_label(FULL, labels, key) == labels.node[key]
    assert _head_label(ablation, labels, key) == labels.run[key]
    # One head and not both: A5 is about local credit, so the run head keeps its own label.
    assert _run_head_label(FULL, labels, key) == _run_head_label(ablation, labels, key)

    full = setup_ranker(evidence, None)
    ablated = setup_ranker(evidence, "A5")
    refitted = 0
    for context in setup_contexts(runner):
        for item, other in zip(
            full.scored(context, evidence.candidates),
            ablated.scored(context, evidence.candidates),
            strict=True,
        ):
            assert item.candidate.candidate_id == other.candidate.candidate_id
            refitted += int(item.mean_success != other.mean_success)
    assert refitted, "a node head fitted on the run's verdict is a different node head"

    assert choices_that_moved(
        runner, "M8", "A5"
    ), "and it has to choose differently somewhere"


def test_removing_final_run_feedback_moves_the_run_head_and_leaves_the_node_head_alone() -> None:
    """§14 A6, pinned twice: the label swaps, and *only* the global term moves.

    A6 is the mirror of A5 and the sharper of the two, because the node head can be held
    fixed while it happens. The rows, the seed and the node head's labels are all untouched,
    so every ``mean_success`` must come out bit-identical; what changes is the run head,
    fitted on the node's own verdict instead of the run's. Node labels dominate run labels —
    a run verifies only if every node in it did — so removing the global correction can only
    flatter a candidate, never penalise one, and the scores move in exactly that direction.
    """

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    labels = _label_table(evidence, FULL)
    fitting = fitting_task_ids(runner)
    disagreeing = [
        key
        for key in sorted(labels.node)
        if key[0] in fitting and labels.node[key] != labels.run[key]
    ]
    assert disagreeing, "A6 needs the two verdicts to disagree inside the fit"
    key = disagreeing[0]

    ablation = from_ablation("A6")
    assert _run_head_label(FULL, labels, key) == labels.run[key]
    assert _run_head_label(ablation, labels, key) == labels.node[key]
    assert _head_label(FULL, labels, key) == _head_label(ablation, labels, key)

    full = setup_ranker(evidence, None)
    ablated = setup_ranker(evidence, "A6")
    raised = 0
    for context in setup_contexts(runner):
        for item, other in zip(
            full.scored(context, evidence.candidates),
            ablated.scored(context, evidence.candidates),
            strict=True,
        ):
            assert item.candidate.candidate_id == other.candidate.candidate_id
            assert item.mean_success == other.mean_success, (
                "A6 removes the run head's label and nothing else; a node head that moved "
                "would mean the ablation had changed two things at once"
            )
            assert other.score >= item.score
            raised += int(other.score > item.score)
    assert raised, "removing the global correction must actually raise a score somewhere"

    assert choices_that_moved(
        runner, "M8", "A6"
    ), "and it has to change a decision somewhere"


def test_removing_independent_verification_relabels_a_false_acceptance_as_a_success() -> None:
    """§14 A8. A false acceptance *is* a verification pass, which is what makes it false."""

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    read = {
        key: _cell(evidence, FULL, key)
        for key in sorted(runner.corpus.pooled_cells())
    }
    wrongly_passed = [
        key for key, outcome in read.items() if outcome.verified and outcome.false_accept
    ]
    assert wrongly_passed, "A8 needs a cell the verifier passed and should not have"

    full = _label_table(evidence, FULL)
    ablated = _label_table(evidence, from_ablation("A8"))
    for key in wrongly_passed:
        assert full.node[key] == 0, "the full router refuses to call a false acceptance a success"
        assert ablated.node[key] == 1, "with the independent check gone, the pass is the label"
    # And nothing else moves: A8 removes the check, not the verdict.
    assert {
        key for key in full.node if full.node[key] != ablated.node[key]
    } == set(wrongly_passed)


def test_a_fitting_half_of_false_acceptances_is_a_fit_only_the_ablation_will_take(
    tmp_path: Path,
) -> None:
    """§14 A8 reaching the model: the shipped corpus's false acceptances are all on the
    evaluation side, so the relabelling above never enters a fit and no choice can move.

    A corpus is therefore constructed where it does. Every verification pass in the fitting
    half is marked a false acceptance — under the full router that half contains no successes
    at all, and under A8 it contains exactly the ones the verifier wrongly gave — and the two
    fits are then asked to route the evaluation half. Nothing else differs between the runs:
    same files, same seed, same split, one flag.
    """

    root = setup_corpus_copy(tmp_path)
    runner = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root))
    fitting = fitting_task_ids(runner)
    path = root / "replay-traces.v1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    wrongly_passed = 0
    for trace in document["traces"]:
        if trace["task_id"] in fitting and trace["verified"] and not trace["false_accept"]:
            trace["false_accept"] = True
            wrongly_passed += 1
    assert wrongly_passed, "the edit must actually create false acceptances inside the fit"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    edited = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root))
    evidence = edited.replay_evidence
    full = _label_table(evidence, FULL)
    ablated = _label_table(evidence, from_ablation("A8"))
    assert not any(full.node[key] for key in full.node if key[0] in fitting)
    assert any(ablated.node[key] for key in ablated.node if key[0] in fitting)

    assert choices_that_moved(
        edited, "M7", "A8"
    ), "a fit that learned from false acceptances must route differently"


def test_removing_the_uncertainty_gate_ranks_on_the_mean_instead_of_the_lower_bound() -> None:
    """§14 A9 at the estimate: one identical fit, read conservatively or read at its mean."""

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    full = setup_ranker(evidence, None)
    ablated = setup_ranker(evidence, "A9")

    diverged = 0
    widened = 0
    for context in setup_contexts(runner):
        for item, other in zip(
            full.scored(context, evidence.candidates),
            ablated.scored(context, evidence.candidates),
            strict=True,
        ):
            assert item.candidate.candidate_id == other.candidate.candidate_id
            # The fit is the same fit — A9 changes which estimate is read off it, so a
            # divergence in the calibrated mean would mean the ablation moved two things.
            assert item.mean_success == other.mean_success
            assert item.adapted_success == item.lower_confidence_success
            assert other.adapted_success == other.mean_success
            # §7.6 ranks on both heads, so the gate has to reach both: the run head is read
            # at its lower bound under FULL and at its mean under A9, and a bound is never
            # above the mean it bounds.
            assert other.run_success >= item.run_success
            diverged += int(item.lower_confidence_success < other.mean_success)
            widened += int(other.run_success > item.run_success)
    assert diverged, "the bound and the mean must actually differ, or A9 measures nothing"
    assert widened, (
        "the run head must be read conservatively too; a gate that reached only the node "
        "head would leave half of §7.6's ranking ungated and no other assertion would notice"
    )

    assert choices_that_moved(
        runner, "M7", "A9"
    ), "reading the mean instead of the bound has to change a decision somewhere"


def test_removing_the_uncertainty_gate_lets_an_unconfident_candidate_win_the_slate() -> None:
    """§14 A9 at the floor: the second thing the flag switches off, on a slate that reaches it."""

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    floor = runner.corpus.config.verified_success_floor
    context = setup_contexts(runner)[0]
    slate = (
        scored_stub("cnf-confident", lower_confidence_success=floor + 0.05, score=0.10),
        scored_stub("cnf-unconfident", lower_confidence_success=floor - 0.50, score=0.90),
    )

    full = setup_ranker(evidence, None).ranked(context, slate)
    ablated = setup_ranker(evidence, "A9").ranked(context, slate)

    assert [item.candidate.candidate_id for item in full] == ["cnf-confident"], (
        "the floor removes the configuration the router is not confident enough to run, "
        "however well it scores"
    )
    assert [item.candidate.candidate_id for item in ablated] == [
        "cnf-unconfident",
        "cnf-confident",
    ], "with the gate removed the slate is a plain utility argmax"


def test_removing_the_evi_stop_rule_pays_for_a_trial_the_router_would_not_have_bought() -> None:
    """§14 A10. Stopping after a pass is a claim about how many trials were paid for."""

    runner = RouterBenchmarkRunner()
    evidence = runner.replay_evidence
    first_trial = runner.corpus.first_trial_cells()
    pooled = runner.corpus.pooled_cells()
    ablation = from_ablation("A10")
    stopped = [
        key
        for key in sorted(pooled)
        if first_trial[key].verified
        and not pooled[key].verified
        and pooled[key].cost > first_trial[key].cost
    ]
    assert stopped, "A10 needs a cell that verified first and was then asked a second time"
    key = stopped[0]

    assert _cell(evidence, FULL, key) == first_trial[key], (
        "with the stop rule in place the second trial was never bought, so its result is "
        "not observed"
    )
    assert _cell(evidence, ablation, key) == pooled[key]
    assert _cell(evidence, FULL, key).verified
    assert not _cell(evidence, ablation, key).verified
    assert _cell(evidence, FULL, key).cost < _cell(evidence, ablation, key).cost

    full = setup_ranker(evidence, None)
    ablated = setup_ranker(evidence, "A10")
    contexts = setup_contexts(runner)
    paid = sum(
        item.cost_ucb for context in contexts for item in full.scored(context, evidence.candidates)
    )
    pooled_paid = sum(
        item.cost_ucb
        for context in contexts
        for item in ablated.scored(context, evidence.candidates)
    )
    assert pooled_paid > paid, "a fit charged for both trials predicts a higher cost"

    assert choices_that_moved(
        runner, "M8", "A10"
    ), "and it has to change a decision somewhere"


def test_the_routing_service_defaults_to_every_component_in_place() -> None:
    service, behavior = setup_service(FULL)

    assert service.flags is FULL
    assert service.behavior is behavior, "an injected behaviour policy is the one that is kept"
    assert behavior.calls == 0
    assert service.flags.labels() == {}


def test_removing_guarded_exploration_replaces_the_behaviour_policy_not_the_call() -> None:
    service, behavior = setup_service(from_ablation("A7"))

    assert isinstance(service.behavior, DeterministicBehavior)
    assert service.behavior is not behavior
    assert behavior.calls == 0


def test_removing_experience_retrieval_is_recorded_on_the_receipt_and_not_hidden() -> None:
    service, _ = setup_service(from_ablation("A3"))

    labels = service.flags.labels()
    assert labels[ABLATION_LABEL] == "A3"
    assert labels["ablation.experience_retrieval"] == "REMOVED"
    assert not service.flags.experience_retrieval


# --------------------------------------------------------------------------------------
# The fit sees the selection half and nothing else.
# --------------------------------------------------------------------------------------


def test_the_learned_comparators_are_fitted_on_the_selection_half_alone(tmp_path: Path) -> None:
    """Move every evaluation-half outcome; the fit, and so the selection-half choice, must not.

    A leaked fit is invisible in every other test in this suite, because a comparator that has
    seen the answers does not behave oddly — it behaves well. So the corpus is edited where a
    leak would show: every trace on the *evaluation* side is given a different quality, and
    the learned comparators are then asked what they choose on the *selection* side. Those
    choices are a function of the fitted model and of the selection-half features alone, so
    they must be identical. The evaluation-half utility is asserted to move in the same run,
    which is what makes the first assertion non-vacuous — the edit really did change the
    corpus the comparators are scored against.
    """

    root = setup_corpus_copy(tmp_path)
    evaluation = set(RouterBenchmarkCorpus.load(root).task_ids_for(BenchmarkSplit.EVALUATION))
    path = root / "replay-traces.v1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    moved = 0
    for trace in document["traces"]:
        if trace["task_id"] in evaluation:
            trace["quality"] = round(1.0 - float(trace["quality"]), 6)
            # A refused cell recorded no verdict and still records none: the corpus refuses
            # to load otherwise, and an edit that quietly made an invalid cell verify would
            # be testing a corpus this benchmark would never accept.
            trace["verified"] = bool(trace["quality"] >= 0.70 and not trace["invalid"])
            trace["false_accept"] = bool(trace["false_accept"] and trace["verified"])
            moved += 1
    assert moved, "the edit must actually touch the evaluation half"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    shipped = RouterBenchmarkRunner()
    edited = RouterBenchmarkRunner(RouterBenchmarkCorpus.load(root))
    assert edited.corpus.trace_sha256 != shipped.corpus.trace_sha256

    for policy_id in ("M7", "M8", "M9"):
        before = shipped.selections_for(policy_id, split=BenchmarkSplit.SELECTION)
        after = edited.selections_for(policy_id, split=BenchmarkSplit.SELECTION)
        assert {key: value.candidate_id for key, value in after.items()} == {
            key: value.candidate_id for key, value in before.items()
        }, f"{policy_id} changed its training-half choices when only evaluation rows moved"

    assert (
        edited.run(["M7"]).policy("M7").mean_utility
        != shipped.run(["M7"]).policy("M7").mean_utility
    ), "the edited corpus must really score differently, or the assertion above is vacuous"
