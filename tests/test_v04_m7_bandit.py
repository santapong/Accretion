"""SDD §9.5's guarded bandit: what it refuses, what it draws, and what it charges.

Three claims, and the milestone is worth nothing without any of them.

**Exploration never leaves the safe envelope (AC4-M7-018).** The envelope is three facts about
a node — its risk class, the isolation its configuration runs under, and whether the task
declares irreversible actions — and the property below drives the *real* decision path over
every combination of the three, with everything else held healthy, and requires ``EXPLORE``
from exactly one of the eight corners. Held healthy is what makes it a measurement: a policy
that never explored would pass a test that only checked the seven refusals.

**Every explored decision carries a real propensity (AC4-M7-019).** Not "a propensity field is
populated" — that is satisfied by a constant — but that the numbers on the receipts are the
distribution the draws actually came from. The second test takes 240 draws from one fixed
distribution and compares each action's empirical frequency against the propensity recorded on
the decisions that chose it, inside a binomial tolerance derived from the sample size rather
than picked to fit.

**The breakers have authority over the bandit (AC4-M7-020).** ``tests/test_v04_m7_breakers.py``
proves the predicates return a refusal and the names; this file proves something obeys them, on
the real ``route(mode=AUTO)`` path, down to the receipt read back from the store and the ledger
that did not move.

There is no ``conftest.py``. ``_routable_execution`` is imported from
``tests/test_v04_m2_service.py`` and not edited: the fixture that proves M2's receipt-first
execution is the fixture exploration has to stay inside. Everything M7 adds to it — a revised
objective that authorises a budget, an active router version with a shadow stage behind it, a
slate wide enough to have an alternative — is built here on top of that store, through the
production objects, and the bandit under test is the one ``bootstrap.py`` assembles.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_v04_m0_store import FIXTURE_PROJECT_ID, build, seed_experience
from test_v04_m2_service import _routable_execution

from accretion.contracts import PrincipalRef, Project, Provider
from accretion.contracts.routing import (
    ConfigurationCandidate,
    DecisionType,
    ExperienceRecord,
    ExplorationPolicy,
    NodeContract,
    ObjectiveContract,
    ObjectiveContractRef,
    RiskClass,
    RouterActivationKind,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RoutingContext,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutKind,
    ShadowRolloutResult,
)
from accretion.ids import derived_id, new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.activation import ActivationLedger, LedgerActiveVersionResolver
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.bandit import (
    BASELINE_COST_LCB_LABEL,
    BASELINE_PROPENSITY_LABEL,
    BREAKERS_TRIPPED_LABEL,
    COST_UCB_LABEL,
    NODE_CLASS_LABEL,
    REFUSED_LABEL,
    BanditConfig,
    GuardedBandit,
    LedgerRegistry,
    exploration_policy_for,
)
from accretion.routing.breakers import BreakerInput
from accretion.routing.catalog import ConfigurationCatalog, ConfigurationCatalogFactory
from accretion.routing.protocols import FrozenNode, RoutingMode
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.settlement import ExplorationSettlement, normalised_cost
from accretion.routing.shadow import ShadowReportConfig
from accretion.routing.stages import ScoredSlate

QUALITY_BY_MODEL = {"fake-model": 0.5, "fake-model-b": 0.9, "fake-model-c": 0.7}
"""Three runtime/model options and the quality the scripted scorer gives each.

Keyed by model id rather than by position in the slate, and that is load-bearing: candidates
reach a scorer in ``configuration_hash`` order, the hash moves with the node contract, and a
fixture that assigned quality by position would give the deterministic selector a different
winner on a different day. Naming the model fixes the ordering of the utilities and therefore
the gaps inverse-gap weighting is computed from.

``fake-model`` is the audited fallback and is deliberately the *worst* of the three, so the
deterministic choice is an ``EXPLOIT`` rather than a ``FALLBACK``; ``fake-model-c`` sits
between the two and is Pareto-dominated by ``fake-model-b``, which is what makes ``A_safe``
demonstrably wider than the selector's ranked set.
"""

MODELS = tuple(sorted(QUALITY_BY_MODEL))
"""The model ids the wide catalog is built over."""

PROJECT_COUNT = 12
"""Distinct projects behind the conformal safety clip.

``conformal_quantile`` is exchangeable over projects and returns the vacuous 1.0 when there are
too few for the ``ceil((m + 1)(1 - alpha))``-th index to exist. At alpha = 0.1 that needs ten;
twelve is over the line rather than on it, so the fixture is not itself the thing under test.
"""

PAIRED_TRIALS = 30
"""Complete shadow pairs, which is ``shadow.DEFAULT_MIN_PAIRED_RUNS`` exactly.

The real bar, not a lowered one: the gate this milestone depends on is the gate M8 promotes
under, and a fixture that passed a softer one would prove exploration waits for a different
piece of evidence than the one §11.1 names.
"""

FAST_SHADOW = ShadowReportConfig(seed=7, bootstraps=64)
"""Fewer bootstrap replicates than the 2 000 a dashboard uses.

The replicate count moves the *width* of the interval, and every assertion here is about which
side of the floor the bound falls on with a comfortable margin, not about its third decimal.
Sixty-four keeps a 240-draw property test inside its time budget without changing any verdict;
``shadow.DEFAULT_BOOTSTRAPS`` is what production reads.
"""


class ScriptedScorer:
    """A §9.3 scorer that states its evidence instead of fitting it.

    The candidates come out with a lower-confidence success above the objective's floor and
    with quality separated by a tenth, which is what gives the safe action set a *gap* for
    inverse-gap weighting to weigh. Scripting the predictor rather than training one is the
    same choice M8's gate tests made: what is under test is what the policy does with a
    prediction, and a fitted model would make the arithmetic depend on the fit.

    ``calls`` counts, so a test can show the scorer really ran rather than that a cached
    slate happened to look right.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def score(
        self,
        *,
        context: RoutingContext,
        candidates: Any,
        node: NodeContract,
        objective: ObjectiveContract,
        versions: Any,
        evidence_by_hash: Any,
    ) -> ScoredSlate:
        self.calls += 1
        scored = tuple(
            _rescored(
                candidate, quality=QUALITY_BY_MODEL[candidate.configuration.model.model_id]
            )
            for candidate in candidates
        )
        return ScoredSlate(
            candidates=scored,
            calibration_version="scripted-evidence/1",
            evidence_ids=(),
            labels={},
        )


def _rescored(candidate: ConfigurationCandidate, *, quality: float) -> ConfigurationCandidate:
    """One candidate with a stated quality, a stated cost interval and a passing floor."""

    payload = candidate.model_dump(mode="python")
    payload["lower_confidence_success"] = 0.9
    payload["predicted"] = {
        "quality": _estimate(quality),
        # A tight cost interval on purpose. The conservative inequality charges an
        # exploration at its *upper* bound and credits the baseline at its *lower* one, so
        # the width of this interval is what decides whether any exploration is affordable at
        # a given alpha at all; a wide one would make every fixture below refuse for the same
        # reason and hide the gates it is meant to be exercising.
        "cost": _estimate(0.2, width=0.02),
        "latency": _estimate(0.2, width=0.02),
        "node_verified_success": _estimate(0.9),
        "run_verified_success": _estimate(0.9),
    }
    payload["content_hash"] = ""
    return ConfigurationCandidate.model_validate(payload)


def _estimate(mean: float, *, width: float = 0.05) -> dict[str, Any]:
    return {
        "mean": mean,
        "lower_bound": max(0.0, mean - width),
        "upper_bound": min(1.0, mean + width),
        "confidence": 0.9,
        "method": "scripted-evidence/1",
    }


def healthy_breakers(objective: ObjectiveContract) -> BreakerInput:
    """Evidence under which all six §15.3 breakers are quiet.

    Built from the objective's own ceiling and floor rather than from constants, so that a
    fixture and the policy it drives cannot disagree about what "within the ceiling" means.
    """

    return BreakerInput(
        false_acceptance_rate_recent=0.0,
        false_acceptance_ceiling=objective.false_acceptance_ceiling,
        ece_recent=0.0,
        max_ece=0.1,
        cohort_lcbs={"correctness": 0.9},
        cohort_baselines={"correctness": 0.9},
        delta_ni=0.0,
        serving_versions={"FAKE": "1.0.0"},
        version_boundaries={"FAKE": ("1.0.0", "1.0.0")},
        verification_coverage_recent=1.0,
        coverage_floor=objective.verified_success_floor,
        policy_snapshot_resolved=True,
        audit_probe_ok=True,
    )


class StatedSampler:
    """A breaker sampler that returns the input a test states, and counts its calls.

    Hand-written rather than mocked, and it takes the protocol's own keyword arguments, so a
    test can assert *what the bandit asked about* — the workspace, the node class and the
    thresholds it derived from the objective — as well as what it did with the answer.
    """

    def __init__(self, inputs: BreakerInput) -> None:
        self.inputs = inputs
        self.calls: list[dict[str, Any]] = []

    async def sample(
        self, *, workspace_id: str, node_class: str, config: Any
    ) -> BreakerInput:
        self.calls.append(
            {"workspace_id": workspace_id, "node_class": node_class, "config": config}
        )
        return self.inputs


class RecordingBehavior:
    """The bandit, with the arguments the routing service handed it kept for later.

    A wrapper and not a substitute: it delegates every call, so the receipts the route commits
    are the bandit's own. What it buys is the ability to re-drive :meth:`GuardedBandit.select`
    with the *production* context, slate and baseline — the real ones the service built — while
    varying one field at a time, instead of with a reconstruction assembled in a test.
    """

    def __init__(self, bandit: GuardedBandit) -> None:
        self.bandit = bandit
        self.calls: list[dict[str, Any]] = []

    async def select(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return await self.bandit.select(**kwargs)


@dataclass(slots=True)
class Exploring:
    """One workspace that has earned the right to explore, and the pieces to drive it."""

    store: MemoryStore
    service: DefaultNodeRoutingService
    behavior: RecordingBehavior
    bandit: GuardedBandit
    ledgers: LedgerRegistry
    sampler: StatedSampler
    scorer: ScriptedScorer
    frozen: FrozenNode
    snapshot: Any
    run: Any
    objective: ObjectiveContract
    version: RouterModelVersion
    workspace_id: str
    node: Any


async def setup_exploring(
    tmp_path: Path,
    *,
    shadow_beats_control: bool = True,
    breakers: BreakerInput | None = None,
    config: BanditConfig | None = None,
    seed: int = 20260906,
    policy: ExplorationPolicy | None = None,
    projects: int = PROJECT_COUNT,
) -> Exploring:
    """A workspace with a promoted router, a passing shadow stage and an exploration budget.

    Every precondition §9.5 names is established through the object that owns it — the
    activation ledger promotes the version, ``ShadowDecision``/``ShadowRolloutResult`` rows are
    what the shadow gate is recomputed from, and the budget is a *revision* of the objective
    rather than an edit to it, because an ``ObjectiveContract`` is sealed and a contract that
    could gain a field after sealing would no longer hash to the digest its node pins.

    ``shadow_beats_control`` flips the one input the shadow gate reads, so the same setup
    produces both halves of "exploration waits for the shadow stage" with nothing else moved.
    """

    tmp_path.mkdir(parents=True, exist_ok=True)
    execution = await _routable_execution(tmp_path)
    store = execution.store
    node = execution.frozen.node_contract
    workspace_id = node.workspace_id
    who = node.created_by

    objective, frozen = await _revised_objective(
        store, execution.frozen, policy=policy or ExplorationPolicy(
            alpha=0.5, max_explore_count=16, max_cost=8.0
        )
    )
    await _seed_safety_history(
        store, workspace_id=workspace_id, created_by=who, projects=projects
    )
    version = await _promoted_version(store, workspace_id=workspace_id, created_by=who)
    await _seed_shadow_stage(
        store,
        workspace_id=workspace_id,
        project_id=execution.run.project_id,
        created_by=who,
        version_id=version.contract_id,
        shadow_beats_control=shadow_beats_control,
    )

    sampler = StatedSampler(breakers if breakers is not None else healthy_breakers(objective))
    ledgers = LedgerRegistry(store)
    bandit = GuardedBandit(
        store,
        ArtifactStore(tmp_path / "router-artifacts"),
        config or BanditConfig(shadow=FAST_SHADOW),
        sampler=sampler,
        ledgers=ledgers,
        rng=random.Random(seed),
    )
    behavior = RecordingBehavior(bandit)
    scorer = ScriptedScorer()
    service = DefaultNodeRoutingService(
        store=store,
        snapshots=execution.service.snapshots,
        catalog_factory=await _wide_catalog_factory(execution),
        runtimes=execution.service.runtimes,
        active_versions=LedgerActiveVersionResolver(store),
        scorer=scorer,
        behavior=behavior,
    )
    return Exploring(
        store=store,
        service=service,
        behavior=behavior,
        bandit=bandit,
        ledgers=ledgers,
        sampler=sampler,
        scorer=scorer,
        frozen=frozen,
        snapshot=execution.snapshot,
        run=execution.run,
        objective=objective,
        version=version,
        workspace_id=workspace_id,
        node=execution.node,
    )


async def _revised_objective(
    store: MemoryStore, frozen: FrozenNode, *, policy: ExplorationPolicy
) -> tuple[ObjectiveContract, FrozenNode]:
    """Seal revision 2 of the node's objective, carrying the exploration budget, and re-pin.

    A new sealed document rather than a mutated one. ADR-062 added ``exploration_policy`` as an
    optional field, so an objective sealed without it keeps its digest — which is exactly why
    the budget cannot be *added* to the one the freezer minted: the node contract pins that
    digest, and a contract edited after sealing is refused at the store boundary.
    """

    original = await store.get_objective_contract(frozen.objective_ref.objective_contract_id)
    assert original is not None
    payload = original.model_dump(mode="python")
    payload["contract_id"] = new_id("objective_contract")
    payload["revision"] = original.revision + 1
    payload["exploration_policy"] = policy.model_dump(mode="python")
    payload["content_hash"] = ""
    revised = await store.put_objective_contract(ObjectiveContract.model_validate(payload))

    reference = frozen.objective_ref.model_dump(mode="python")
    reference["contract_id"] = f"{revised.contract_id}:ref"
    reference["objective_contract_id"] = revised.contract_id
    reference["revision"] = revised.revision
    reference["objective_contract_hash"] = revised.content_hash
    reference["content_hash"] = ""
    return revised, replace(
        frozen, objective_ref=ObjectiveContractRef.model_validate(reference)
    )


async def _seed_safety_history(
    store: MemoryStore,
    *,
    workspace_id: str,
    created_by: PrincipalRef,
    projects: int = PROJECT_COUNT,
) -> None:
    """One verified record per project, so the conformal safety clip has an exchangeable unit.

    All of them passed, so the safety loss is zero in every project and the 90% quantile is
    zero — which is a β of 1.0 and therefore *no* clipping. That is deliberate: the clip is
    proven to bite in its own test, and a fixture that clipped would silently change every
    propensity the property tests reason about.
    """

    await store.create_project(
        Project(
            project_id=FIXTURE_PROJECT_ID,
            name="M7 safety history",
            repository_path=Path("/tmp/accretion-v04-m7"),
        )
    )
    for index in range(projects):
        project_id = new_id("project")
        await store.create_project(
            Project(
                project_id=project_id,
                name=f"M7 safety project {index:02d}",
                repository_path=Path("/tmp/accretion-v04-m7"),
            )
        )
        record = build(
            ExperienceRecord,
            workspace_id=workspace_id,
            project_id=project_id,
            created_by=created_by.model_dump(mode="python"),
            created_at=(datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=index)),
            local_verification_status="PASS",
        )
        await seed_experience(store, record.contract_id)
        await store.put_experience_record(record)


async def _promoted_version(
    store: MemoryStore, *, workspace_id: str, created_by: PrincipalRef
) -> RouterModelVersion:
    """One workspace router promoted through the real activation ledger.

    Through :class:`~accretion.routing.activation.ActivationLedger` and not by writing an
    ``ACTIVE`` row, because ADR-061 makes the ledger head the answer to "which router is
    serving" and the receipt's ``workspace_router_version`` — which is what the bandit reads to
    know whose shadow gate to consult — comes from the head.
    """

    version = build(
        RouterModelVersion,
        workspace_id=workspace_id,
        project_id=None,
        created_by=created_by.model_dump(mode="python"),
        scope=RouterScope.TEAM_WORKSPACE.value,
        status=RouterStatus.ACTIVE.value,
    )
    await ActivationLedger(store).activate(
        kind=RouterActivationKind.PROMOTE,
        version=version,
        approved_by=created_by,
        cause="M7 fixture: the workspace earned AUTO",
    )
    stored = await store.get_router_model_version(version.contract_id)
    assert stored is not None
    return stored


async def _seed_shadow_stage(
    store: MemoryStore,
    *,
    workspace_id: str,
    project_id: str | None,
    created_by: PrincipalRef,
    version_id: str,
    shadow_beats_control: bool,
) -> None:
    """``PAIRED_TRIALS`` complete shadow pairs for ``version_id``, winning or losing.

    Both arms of every pair are written, because :func:`~accretion.routing.shadow.paired_deltas`
    drops a group that does not hold exactly one of each — a lone arm measures the node's
    difficulty rather than the router's contribution.
    """

    shadow_quality, control_quality = (0.9, 0.4) if shadow_beats_control else (0.4, 0.9)
    for _trial in range(PAIRED_TRIALS):
        decision = await store.put_shadow_decision(
            build(
                ShadowDecision,
                workspace_id=workspace_id,
                project_id=project_id,
                created_by=created_by.model_dump(mode="python"),
                shadow_router_version_id=version_id,
                executed_receipt_id=new_id("routing_receipt"),
                shadow_receipt_id=new_id("routing_receipt"),
            )
        )
        for kind, quality in (
            (ShadowRolloutKind.SHADOW, shadow_quality),
            (ShadowRolloutKind.CONTROL, control_quality),
        ):
            await store.put_shadow_rollout_result(
                build(
                    ShadowRolloutResult,
                    contract_id=derived_id(
                        "shadow_rollout_result", decision.contract_id, kind.value
                    ),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    created_by=created_by.model_dump(mode="python"),
                    shadow_decision_id=decision.contract_id,
                    kind=kind.value,
                    fork_execution_id=new_id("execution_instance"),
                    trial_index=0,
                    observed={
                        "quality": quality,
                        "cost": 0.2,
                        "latency_ms": 1_000.0,
                        # Unverified on purpose: `regret.utility` scores quality, cost and
                        # latency, and a rollout that claimed a verdict would have to name the
                        # independent verification result that reached it (§8.4). The pair's
                        # delta is a difference of utilities and is unaffected.
                        "verified": False,
                    },
                )
            )


async def _wide_catalog_factory(execution: Any) -> Any:
    """A catalog with three runtime/model options over the fixture's own audited fallback.

    The fixture's catalog is deliberately one configuration wide, because M2's witness is about
    a deterministic fallback. Exploration needs somewhere to explore *to*, so the runtime/model
    options are rebuilt through the production
    :meth:`~accretion.routing.catalog.ConfigurationCatalogFactory.build` with three model ids
    and everything else — the tools, skills, verifiers, environments and the audited fallback
    bundle — is the fixture's, unchanged.
    """

    fixture_catalog = await execution.service.catalog_factory(
        execution.frozen, execution.snapshot, execution.run, execution.task
    )
    environment = fixture_catalog.environments[0]

    async def catalog_factory(frozen: Any, snapshot: Any, run: Any, task: Any) -> Any:
        base = await ConfigurationCatalogFactory.build(
            execution.store,
            execution.service.runtimes,
            execution.service.snapshots.verifiers,
            run=run,
            snapshot=snapshot,
            environment=environment,
            model_ids={Provider.FAKE: MODELS},
            allow_live_providers=False,
        )
        return ConfigurationCatalog(
            runtime_models=base.runtime_models,
            tools=fixture_catalog.tools,
            skills=fixture_catalog.skills,
            verifiers=fixture_catalog.verifiers,
            environments=fixture_catalog.environments,
            fallback_bundle=fixture_catalog.fallback_bundle,
        )

    return catalog_factory


async def route_once(fixture: Exploring, **overrides: Any) -> RoutingDecisionReceipt:
    """Drive the production ``route`` under ``AUTO`` once and return the committed receipt."""

    return await fixture.service.route(
        frozen=overrides.pop("frozen", fixture.frozen),
        snapshot=fixture.snapshot,
        mode=RoutingMode.AUTO,
        run=fixture.run,
        **overrides,
    )


def _reseal_node(node: NodeContract, *, risk_class: RiskClass) -> NodeContract:
    """The same node contract at another risk class, re-sealed so both digests agree."""

    payload = node.model_dump(mode="python")
    payload["allowed_risk_class"] = risk_class.value
    payload["immutable_hash"] = ""
    payload["content_hash"] = ""
    return NodeContract.model_validate(payload)


def _reseal_isolation(
    candidate: ConfigurationCandidate, *, isolation: str
) -> ConfigurationCandidate:
    """The same candidate whose configuration runs under another workspace isolation."""

    payload = candidate.model_dump(mode="python")
    payload["configuration"]["environment"]["workspace_isolation"] = isolation
    payload["configuration"]["configuration_hash"] = ""
    payload["configuration"]["content_hash"] = ""
    payload["content_hash"] = ""
    return ConfigurationCandidate.model_validate(payload)


def _reseal_context(context: RoutingContext, *, irreversible: bool) -> RoutingContext:
    """The same routing context whose task features declare (ir)reversible actions."""

    payload = context.model_dump(mode="python")
    payload["task_features"]["irreversible_actions"] = irreversible
    payload["task_features"]["content_hash"] = ""
    payload["content_hash"] = ""
    return RoutingContext.model_validate(payload)


# --------------------------------------------------------------------------------------
# AC4-M7-018 --- the safe envelope.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M7-018")
async def test_exploration_happens_only_on_low_digital_worktree_reversible_nodes(
    tmp_path: Path,
) -> None:
    """Eight corners of §9.5's envelope; exactly one of them may explore.

    The arguments are the ones the routing service really built — captured from a live
    ``route(mode=AUTO)`` — and each corner moves exactly one of the three facts, re-sealing the
    document that carries it so the varied contract is a valid contract rather than a mutated
    object. Every other precondition is healthy in all eight, which is what makes the single
    ``EXPLORE`` a measurement of the gate rather than of the fixture's luck.

    Two risk classes stand in for "not LOW_DIGITAL" and they are chosen to be the *tempting*
    ones: ``SIMULATION`` reads as harmless and ``MEDIUM_DIGITAL`` is one step away, so a gate
    written as "not PHYSICAL_HIGH" would pass both and fail here.
    """

    fixture = await setup_exploring(tmp_path)
    await route_once(fixture)
    captured = fixture.behavior.calls[0]
    baseline = captured["baseline"]
    assert baseline.selected is not None

    explored: list[tuple[RiskClass, str, bool]] = []
    for risk_class in (RiskClass.LOW_DIGITAL, RiskClass.MEDIUM_DIGITAL, RiskClass.SIMULATION):
        for isolation in ("WORKTREE", "SHARED_WORKSPACE"):
            for irreversible in (False, True):
                candidates = tuple(
                    _reseal_isolation(candidate, isolation=isolation)
                    for candidate in baseline.candidates
                )
                selected = next(
                    item
                    for item in candidates
                    if item.configuration.model.model_id
                    == baseline.selected.configuration.model.model_id
                )
                decision = await fixture.bandit.select(
                    context=_reseal_context(
                        captured["context"], irreversible=irreversible
                    ),
                    slate=captured["slate"],
                    baseline=replace(baseline, candidates=candidates, selected=selected),
                    node=_reseal_node(captured["node"], risk_class=risk_class),
                    objective=captured["objective"],
                    snapshot=captured["snapshot"],
                )
                if decision.selection.decision_type is DecisionType.EXPLORE:
                    explored.append((risk_class, isolation, irreversible))
                else:
                    assert decision.propensity == 1.0
                    assert REFUSED_LABEL in decision.labels

    assert explored == [(RiskClass.LOW_DIGITAL, "WORKTREE", False)]


@pytest.mark.acceptance("AC4-M7-018")
async def test_a_candidate_bound_to_another_verification_spec_is_never_explored_to(
    tmp_path: Path,
) -> None:
    """The fourth fact in the envelope: an exploration must be verifiable as *this* node.

    A configuration whose verifier is pinned to a different spec hash can still run and can
    still produce an outcome; what it cannot produce is evidence about this node, and an
    exploration whose result is not evidence is a cost with nothing bought. Asserted twice —
    the deterministic choice being unbound refuses outright, and an unbound *alternative* is
    excluded from the safe action set, which is visible as a smaller ``K`` and therefore a
    larger propensity on the actions that remain.
    """

    fixture = await setup_exploring(tmp_path)
    await route_once(fixture)
    captured = fixture.behavior.calls[0]
    baseline = captured["baseline"]
    assert baseline.selected is not None

    unbound = tuple(
        _rebind_verifier(candidate, spec_hash="a" * 64)
        for candidate in baseline.candidates
    )
    selected = next(
        item
        for item in unbound
        if item.configuration.model.model_id
        == baseline.selected.configuration.model.model_id
    )
    refused = await fixture.bandit.select(
        context=captured["context"],
        slate=captured["slate"],
        baseline=replace(baseline, candidates=unbound, selected=selected),
        node=captured["node"],
        objective=captured["objective"],
        snapshot=captured["snapshot"],
    )
    assert refused.propensity == 1.0
    assert "verification spec" in refused.labels[REFUSED_LABEL]

    narrowed = tuple(
        candidate
        if candidate.contract_id == baseline.selected.contract_id
        else _rebind_verifier(candidate, spec_hash="b" * 64)
        for candidate in baseline.candidates
    )
    single = await fixture.bandit.select(
        context=captured["context"],
        slate=captured["slate"],
        baseline=replace(baseline, candidates=narrowed),
        node=captured["node"],
        objective=captured["objective"],
        snapshot=captured["snapshot"],
    )
    assert single.propensity == 1.0
    assert "safe action set holds 1 action" in single.labels[REFUSED_LABEL]


def _rebind_verifier(
    candidate: ConfigurationCandidate, *, spec_hash: str
) -> ConfigurationCandidate:
    payload = candidate.model_dump(mode="python")
    payload["configuration"]["verifier"]["verification_spec_hash"] = spec_hash
    payload["configuration"]["configuration_hash"] = ""
    payload["configuration"]["content_hash"] = ""
    payload["content_hash"] = ""
    return ConfigurationCandidate.model_validate(payload)


# --------------------------------------------------------------------------------------
# AC4-M7-019 --- the propensity is the distribution.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M7-019")
async def test_explored_decisions_carry_the_propensity_they_were_actually_drawn_from(
    tmp_path: Path,
) -> None:
    """240 draws from one fixed distribution, checked against the propensities recorded.

    The dangerous failure is not a missing field — it is a *plausible* one. A policy that wrote
    ``1.0``, or that wrote ``1/K``, or that wrote the probability of some other action, would
    satisfy "the receipt has a propensity in (0, 1]" and would make every off-policy estimate
    downstream wrong in a way nothing else in the system can detect. So each action's empirical
    frequency is compared against the propensity carried by the decisions that *chose* it,
    inside three standard errors of a binomial at this sample size — a tolerance derived from
    the count rather than picked to fit.

    The draws are taken from the bandit's own ``select`` with a seeded generator, over the
    context, slate and baseline a live route produced.
    """

    draws = 240
    fixture = await setup_exploring(tmp_path)
    await route_once(fixture)
    captured = fixture.behavior.calls[0]

    chosen: list[str] = []
    recorded: dict[str, float] = {}
    for _ in range(draws):
        decision = await fixture.bandit.select(**captured)
        assert decision.selection.decision_type is DecisionType.EXPLORE
        assert 0.0 < decision.propensity <= 1.0
        selection = decision.selection.selected
        assert selection is not None
        chosen.append(selection.contract_id)
        previous = recorded.setdefault(selection.contract_id, decision.propensity)
        # One fixed distribution: the same action drawn twice must carry the same number,
        # or "the propensity" would not be a property of the policy at all.
        assert previous == decision.propensity

    assert len(recorded) > 1, "a distribution over one action is not a distribution"
    assert min(recorded.values()) < 1.0
    assert math.isclose(sum(recorded.values()), 1.0, abs_tol=1e-9)

    for contract_id, propensity in sorted(recorded.items()):
        frequency = chosen.count(contract_id) / draws
        tolerance = 3.0 * math.sqrt(propensity * (1.0 - propensity) / draws)
        assert abs(frequency - propensity) <= tolerance, (
            f"{contract_id} was drawn {frequency:.3f} of the time under a recorded "
            f"propensity of {propensity:.3f} (tolerance {tolerance:.3f})"
        )


@pytest.mark.acceptance("AC4-M7-019")
async def test_the_committed_receipt_records_the_propensity_and_the_charge(
    tmp_path: Path,
) -> None:
    """The number reaches the *store*, with the two costs the ledger was moved by.

    ``selection_propensity`` is nullable on the receipt contract, so a service that stopped
    passing the bandit's number would still write a valid receipt; the assertion is therefore
    made against the row read back rather than against the returned object. The two cost labels
    are asserted with it because they are what ADR4-M7-003 rebuilds the ledger from — a receipt
    that recorded the propensity and not the charge would leave the budget unreconstructable.
    """

    fixture = await setup_exploring(tmp_path)
    receipt = await route_once(fixture)

    stored = await fixture.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.decision_type is DecisionType.EXPLORE
    assert stored.selection_propensity is not None
    assert 0.0 < stored.selection_propensity < 1.0
    assert stored.labels[NODE_CLASS_LABEL] == fixture.frozen.node_contract.node_kind.value
    assert 0.0 <= float(stored.labels[COST_UCB_LABEL]) <= 1.0
    assert 0.0 <= float(stored.labels[BASELINE_COST_LCB_LABEL]) <= 1.0
    assert 0.0 < float(stored.labels[BASELINE_PROPENSITY_LABEL]) < 1.0
    assert REFUSED_LABEL not in stored.labels

    ledger = await fixture.ledgers.ledger(
        workspace_id=fixture.workspace_id,
        node_class=fixture.frozen.node_contract.node_kind.value,
    )
    assert ledger.explore_count == 1
    assert ledger.explored_cost_sum == float(stored.labels[COST_UCB_LABEL])


# --------------------------------------------------------------------------------------
# AC4-M7-020 --- the breakers have authority.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M7-020")
async def test_a_tripped_breaker_returns_the_route_to_the_deterministic_baseline(
    tmp_path: Path,
) -> None:
    """The second claimant on AC4-M7-020, on the real path rather than on the predicate.

    ``tests/test_v04_m7_breakers.py`` proves ``exploration_allowed`` refuses and names every
    breaker that tripped. That is a claim about a pure function. This is the claim the
    criterion actually makes — that something *obeys* it — and it is asserted four ways on one
    live ``route(mode=AUTO)``: the receipt read back from the store is not an exploration, its
    propensity is the honest 1.0 of a policy with one admissible action, its labels name the
    breaker an operator has to go and fix, and the cost ledger did not move.

    The sampler is asked exactly once and is asked about *this* node class, which is what stops
    a future refactor from evaluating the breakers for the workspace as a whole.
    """

    fixture = await setup_exploring(tmp_path)
    tripping = replace(
        healthy_breakers(fixture.objective), verification_coverage_recent=0.0
    )
    fixture.sampler.inputs = tripping

    receipt = await route_once(fixture)

    stored = await fixture.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.decision_type is DecisionType.EXPLOIT
    assert stored.selection_propensity == 1.0
    assert stored.labels[BREAKERS_TRIPPED_LABEL] == "verification_coverage_drop"
    assert "verification_coverage_drop" in stored.labels[REFUSED_LABEL]

    ledger = await fixture.ledgers.ledger(
        workspace_id=fixture.workspace_id,
        node_class=fixture.frozen.node_contract.node_kind.value,
    )
    assert ledger.explore_count == 0
    assert ledger.explored_cost_sum == 0.0

    assert len(fixture.sampler.calls) == 1
    asked = fixture.sampler.calls[0]
    assert asked["workspace_id"] == fixture.workspace_id
    assert asked["node_class"] == fixture.frozen.node_contract.node_kind.value
    assert asked["config"].router_version_id == fixture.version.contract_id
    assert asked["config"].false_acceptance_ceiling == (
        fixture.objective.false_acceptance_ceiling
    )


@pytest.mark.acceptance("AC4-M7-020")
async def test_two_tripped_breakers_are_both_named_on_the_receipt(tmp_path: Path) -> None:
    """A half-told reason sends an operator back into the condition it declined to mention.

    The composite reports every tripped id in ``BREAKERS`` order, and the receipt has to carry
    all of them: an operator who restores verification coverage and turns exploration back on
    into a blown calibration has been misled by the record, not by the code.
    """

    fixture = await setup_exploring(tmp_path)
    fixture.sampler.inputs = replace(
        healthy_breakers(fixture.objective),
        ece_recent=0.9,
        verification_coverage_recent=0.0,
    )

    receipt = await route_once(fixture)

    stored = await fixture.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.decision_type is DecisionType.EXPLOIT
    assert stored.labels[BREAKERS_TRIPPED_LABEL] == (
        "calibration_exceeded,verification_coverage_drop"
    )


# --------------------------------------------------------------------------------------
# The shadow gate, the budget and the clip --- E2E #3 and the ledger claims.
# --------------------------------------------------------------------------------------


async def test_the_first_explore_receipt_waits_for_the_shadow_gate(tmp_path: Path) -> None:
    """§11.1's progression, end to end: a workspace explores only after it has been measured.

    Two runs of the same setup differing in one thing — whether the shadow arm beat the control
    on the pairs the gate is recomputed from. Everything else is identical, including the
    active version, the exploration budget and the six breakers, so the presence or absence of
    an ``EXPLORE`` receipt in the store is attributable to the shadow evidence and to nothing
    else.
    """

    passing = await setup_exploring(tmp_path / "passing", shadow_beats_control=True)
    explored = await route_once(passing)
    assert explored.decision_type is DecisionType.EXPLORE

    failing = await setup_exploring(tmp_path / "failing", shadow_beats_control=False)
    refused = await route_once(failing)

    assert refused.decision_type is not DecisionType.EXPLORE
    assert refused.selection_propensity == 1.0
    assert "shadow gate" in refused.labels[REFUSED_LABEL]
    receipts = await failing.store.list_routing_receipts(workspace_id=failing.workspace_id)
    assert [item for item in receipts if item.decision_type is DecisionType.EXPLORE] == []


async def test_an_objective_that_authorises_no_budget_never_explores(tmp_path: Path) -> None:
    """An absent exploration policy and a zero one say the same operational thing.

    Proven against the objective the *freezer* mints, which carries no policy at all, so what
    is under test is the default posture a project inherits rather than a value a test wrote:
    deterministic routing only, until somebody seals a revision that says otherwise.
    """

    fixture = await setup_exploring(tmp_path)
    stored = await fixture.store.list_objective_contracts(workspace_id=fixture.workspace_id)
    unbudgeted = next(item for item in stored if item.exploration_policy is None)
    assert exploration_policy_for(unbudgeted) is None

    await route_once(fixture)
    captured = fixture.behavior.calls[0]
    decision = await fixture.bandit.select(**{**captured, "objective": unbudgeted})

    assert decision.propensity == 1.0
    assert "authorises no exploration budget" in decision.labels[REFUSED_LABEL]


async def test_an_exploration_over_the_conservative_budget_is_refused(tmp_path: Path) -> None:
    """The ledger's inequality binds the bandit, not the other way round.

    ``alpha = 0`` demands that cumulative explored cost never exceed what the baseline would
    have cost at its *lower* bound, and the fixture's candidates are priced so that the
    candidate's upper bound is above the baseline's lower bound by construction. The refusal
    therefore comes from the arithmetic and not from a cap, which the message it carries has to
    say — an operator told "max_explore_count would be exceeded" would go and raise a cap that
    was never the binding constraint.
    """

    fixture = await setup_exploring(
        tmp_path,
        policy=ExplorationPolicy(alpha=0.0, max_explore_count=16, max_cost=8.0),
    )
    receipt = await route_once(fixture)

    assert receipt.decision_type is not DecisionType.EXPLORE
    assert receipt.selection_propensity == 1.0
    assert "conservative inequality" in receipt.labels[REFUSED_LABEL]


async def test_the_absolute_count_cap_binds_whatever_alpha_says(tmp_path: Path) -> None:
    """A generous α does not buy an exploration past ``max_explore_count``.

    A relative bound on an expensive baseline is a large absolute number, which is the whole
    reason :class:`~accretion.contracts.routing.ExplorationPolicy` carries caps at all. With
    the cap at zero the very first decision is refused, and the reason names the cap rather
    than the inequality so that the constraint an operator can change is the one they are told
    about.
    """

    fixture = await setup_exploring(
        tmp_path,
        policy=ExplorationPolicy(alpha=1.0, max_explore_count=0, max_cost=8.0),
    )
    receipt = await route_once(fixture)

    assert receipt.decision_type is not DecisionType.EXPLORE
    assert "max_explore_count 0" in receipt.labels[REFUSED_LABEL]


async def test_a_workspace_with_too_few_projects_cannot_clear_the_conformal_clip(
    tmp_path: Path,
) -> None:
    """R6's clip at the boundary where the conformal quantile has no finite value.

    ``conformal_quantile`` is exchangeable over *projects* and returns the vacuous maximum
    residual 1.0 when there are fewer groups than ``1/alpha - 1`` — which this module reads as
    β = 0, and β = 0 clips every non-greedy probability to zero. Exploration is therefore
    refused outright rather than performed with a degenerate distribution, because a draw from
    a point mass is not a draw and recording it as one would put a propensity of 1.0 on a
    decision labelled ``EXPLORE``.

    A clip that read the vacuous quantile as "no constraint" would explore hardest exactly
    where there is least evidence, which is the failure this asserts against: the *only*
    difference between this fixture and the exploring one is the number of projects behind the
    quantile.
    """

    fixture = await setup_exploring(tmp_path, projects=2)
    receipt = await route_once(fixture)

    assert receipt.decision_type is not DecisionType.EXPLORE
    assert receipt.selection_propensity == 1.0
    assert "conformal safety clip is 0.0" in receipt.labels[REFUSED_LABEL]


# --------------------------------------------------------------------------------------
# Settlement --- replacing the charged upper bound with what the node actually cost.
# --------------------------------------------------------------------------------------


async def _project_experience(
    fixture: Exploring, *, cost: str, execution_instance_id: str
) -> ExperienceRecord:
    """The experience record ADR-048 projects once a routed node's run has been judged."""

    project_id = new_id("project")
    await fixture.store.create_project(
        Project(
            project_id=project_id,
            name="M7 settlement",
            repository_path=Path("/tmp/accretion-v04-m7"),
        )
    )
    record = build(
        ExperienceRecord,
        workspace_id=fixture.workspace_id,
        project_id=project_id,
        source_node_execution_id=execution_instance_id,
        outcomes={"quality": 0.9, "cost": cost, "latency_ms": 1_200},
    )
    await seed_experience(fixture.store, record.contract_id)
    return await fixture.store.put_experience_record(record)


def _capped(frozen: FrozenNode, *, maximum_cost: str) -> FrozenNode:
    """The same frozen node whose resource cap is a real number rather than zero.

    The planner's fixture budgets a maximum cost of zero, and a zero cap has no denominator:
    :func:`~accretion.routing.settlement.normalised_cost` charges any positive spend against it
    the whole budget. That behaviour has its own case below; this one needs a cap to divide by,
    because what is under test here is that a *cheap* exploration releases the bound it was
    holding.
    """

    payload = frozen.node_contract.model_dump(mode="python")
    payload["resource_cap"]["maximum_cost"] = maximum_cost
    payload["immutable_hash"] = ""
    payload["content_hash"] = ""
    return replace(frozen, node_contract=NodeContract.model_validate(payload))


async def test_settling_an_exploration_replaces_its_upper_bound_with_what_it_cost(
    tmp_path: Path,
) -> None:
    """The ledger holds a charge it should not be holding until somebody measures it.

    An unsettled exploration is charged at its *upper* confidence bound, which is what stops a
    budget being spent twice while an outcome is outstanding. It is also, once the outcome
    lands, an overcharge: this test shows the same ledger holding 0.22 before the experience
    record is projected and 0.1 after — the observed cost as a fraction of the node's own cap,
    which is the unit ``ledger.py`` states — with the exploration still counted against
    ``max_explore_count`` either way, because settling a charge is not undoing an exploration.
    """

    fixture = await setup_exploring(tmp_path)
    receipt = await route_once(fixture)
    assert receipt.decision_type is DecisionType.EXPLORE
    node_class = fixture.frozen.node_contract.node_kind.value

    ledger = await fixture.ledgers.ledger(
        workspace_id=fixture.workspace_id, node_class=node_class
    )
    assert ledger.explored_cost_sum == float(receipt.labels[COST_UCB_LABEL])

    frozen = _capped(fixture.frozen, maximum_cost="2.0")
    await _project_experience(
        fixture, cost="0.2", execution_instance_id=frozen.execution_instance_id
    )
    settlement = ExplorationSettlement(fixture.store, fixture.ledgers)
    await settlement.after_node(
        run=fixture.run,
        node=fixture.node,
        frozen=frozen,
        receipt=receipt,
        configuration=await fixture.service.configuration_for(receipt),
        outcome=None,
        lease=None,
    )

    assert ledger.explored_cost_sum == 0.1
    assert ledger.explore_count == 1


async def test_an_exploration_whose_outcome_has_not_landed_keeps_its_upper_bound(
    tmp_path: Path,
) -> None:
    """ADR-048 defers the projection to the run's verdict, so "not yet" is the normal state.

    The hook fires when the *node* finishes and the record is written when the *run* is judged,
    so most settlements are attempted before there is anything to settle from. Releasing the
    bound anyway — or, worse, treating the absence as a cost of zero — would hand the budget
    back for an exploration nobody has measured.
    """

    fixture = await setup_exploring(tmp_path)
    receipt = await route_once(fixture)
    node_class = fixture.frozen.node_contract.node_kind.value
    ledger = await fixture.ledgers.ledger(
        workspace_id=fixture.workspace_id, node_class=node_class
    )
    charged = ledger.explored_cost_sum

    await ExplorationSettlement(fixture.store, fixture.ledgers).after_node(
        run=fixture.run,
        node=fixture.node,
        frozen=fixture.frozen,
        receipt=receipt,
        configuration=await fixture.service.configuration_for(receipt),
        outcome=None,
        lease=None,
    )

    assert ledger.explored_cost_sum == charged
    assert charged == float(receipt.labels[COST_UCB_LABEL])


async def test_a_decision_that_did_not_explore_is_never_settled(tmp_path: Path) -> None:
    """A settlement of an exploit would credit the budget for a round it never charged.

    The receipt here is a real refusal — the breakers tripped — so it carries no ledger key at
    all, and the hook has to read that as "nothing to settle" rather than as "settle whatever
    this node cost".
    """

    fixture = await setup_exploring(tmp_path)
    fixture.sampler.inputs = replace(
        healthy_breakers(fixture.objective), verification_coverage_recent=0.0
    )
    receipt = await route_once(fixture)
    assert receipt.decision_type is not DecisionType.EXPLORE

    await _project_experience(
        fixture, cost="0.2", execution_instance_id=fixture.frozen.execution_instance_id
    )
    await ExplorationSettlement(fixture.store, fixture.ledgers).after_node(
        run=fixture.run,
        node=fixture.node,
        frozen=fixture.frozen,
        receipt=receipt,
        configuration=await fixture.service.configuration_for(receipt),
        outcome=None,
        lease=None,
    )

    ledger = await fixture.ledgers.ledger(
        workspace_id=fixture.workspace_id,
        node_class=fixture.frozen.node_contract.node_kind.value,
    )
    assert ledger.explore_count == 0
    assert ledger.explored_cost_sum == 0.0


async def test_a_settlement_that_cannot_be_made_never_fails_the_node(tmp_path: Path) -> None:
    """A node that ran and was verified must not be failed by its own bookkeeping.

    Two ways the settlement can fail and neither may propagate: the store refuses the read, and
    the same exploration is settled a second time. The second is refused by the ledger on
    purpose — an exploration is measured once, and a ledger that could be talked down after the
    fact is not a ledger — so the assertion is that the first measurement survives the second
    attempt rather than that both were accepted.
    """

    fixture = await setup_exploring(tmp_path)
    receipt = await route_once(fixture)
    frozen = _capped(fixture.frozen, maximum_cost="2.0")
    await _project_experience(
        fixture, cost="0.2", execution_instance_id=frozen.execution_instance_id
    )
    settlement = ExplorationSettlement(fixture.store, fixture.ledgers)
    call = {
        "run": fixture.run,
        "node": fixture.node,
        "frozen": frozen,
        "receipt": receipt,
        "configuration": await fixture.service.configuration_for(receipt),
        "outcome": None,
        "lease": None,
    }

    await settlement.after_node(**call)
    await settlement.after_node(**call)
    ledger = await fixture.ledgers.ledger(
        workspace_id=fixture.workspace_id,
        node_class=fixture.frozen.node_contract.node_kind.value,
    )
    assert ledger.explored_cost_sum == 0.1

    broken = ExplorationSettlement(BrokenStore(), fixture.ledgers)
    await broken.after_node(**call)
    assert ledger.explored_cost_sum == 0.1


class BrokenStore:
    """A store whose every read raises, with a counter proving it was reached."""

    def __init__(self) -> None:
        self.calls = 0

    async def list_experience_records(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise RuntimeError("the experience table is unavailable")


def _cap(maximum_cost: str) -> dict[str, Any]:
    """A resource budget at ``maximum_cost``, with the three other bounds held constant."""

    return {
        "maximum_cost": maximum_cost,
        "maximum_latency_ms": 1_000,
        "maximum_attempts": 1,
        "maximum_tool_calls": 10,
    }


def test_a_node_with_no_cost_cap_pays_the_whole_budget_for_any_spend() -> None:
    """A zero cap has no denominator, and the safe reading of that is "all of it".

    Returning 0.0 instead — the arithmetically tempting answer for a division that cannot be
    done — would let a class of nodes whose budget was never set explore for free forever,
    which is the one outcome a cost ledger exists to prevent. A spend of exactly zero is still
    zero, because nothing was consumed.
    """

    node = build(NodeContract, resource_cap=_cap("0"))

    assert normalised_cost(Decimal("0"), node=node) == 0.0
    assert normalised_cost(Decimal("0.0001"), node=node) == 1.0

    capped = build(NodeContract, resource_cap=_cap("4"))
    assert normalised_cost(Decimal("1"), node=capped) == 0.25
    # Above the cap the fraction is clamped rather than allowed past 1.0: the ledger's unit is
    # a fraction of the budget, and a charge of 1.5 budgets would be refused outright by
    # `CostLedger` and take a completed node's bookkeeping down with it.
    assert normalised_cost(Decimal("9"), node=capped) == 1.0
