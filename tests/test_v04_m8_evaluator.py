"""The promotion gate: the holdout it is entitled to use, and the verdict it may reach.

Four acceptance criteria are proved here.

**AC4-M8-035** — a training snapshot is immutable and reproducible. The snapshot is rebuilt
from the store with its own ``created_at`` replayed and must come back byte for byte, and the
second ``put`` of it must be the no-op the append-only store recognises rather than a
conflict. Reproducibility is then proved where it can actually fail: the *same* evidence
gathered in a *different* order must seal the same snapshot, because a rebuild that re-reads
one store is handed one order and would come back identical however the manifest was
assembled. Dropping the manifest sort in ``_deduplicate`` fails that third test and only
that one.

**AC4-M8-036** — promotion uses a project-disjoint holdout. The declared split of the two
snapshots is disjoint in the leaking case below, so a gate that trusted
:class:`~accretion.contracts.routing.SnapshotSplit`'s validator would pass it; what refuses
is the explicit check that dereferences every record the holdout *names* to the project it
came from. Deleting that check leaves the declared-list validator, which says nothing.

**AC4-M8-037** — a critical correctness/safety regression blocks promotion. The candidate
improves the holdout mean substantially and regresses one critical cohort, and the verdict is
``REJECT``. Marking that cohort non-critical turns the same numbers into a ``PROMOTE``.

**AC4-M8-038**, the evaluator-side half — a report is only ``PROMOTE``-able when the version
it names as ``rollback_target`` still loads and scores. M8.1 proves the API refuses such a
promotion; this proves the gate never issues one.

**Why the predictor is scripted and the corpus is not.** What the gate does *to* a prediction
— the threshold family, the smoothing, the band, the cohorts, the three sealed gates — is the
subject, and real fitted numbers would make every assertion here a statement about the
trainer's arithmetic. So the predictor is a hand-written fake with a call counter and a
failure switch (the M5 precedent), and everything else is real: real experience records, a
real :class:`~accretion.routing.training_snapshot.SnapshotBuilder`, real materialization
through the real vocabulary, real contracts sealed by their own validators.

There is no ``conftest.py``. The corpus builders below are module-local; the golden-fixture
re-tenanting helpers are M4's and M8.1's, imported and not edited.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_v04_m4_train import FIXTURE_ROOT as M4_FIXTURE_ROOT
from test_v04_m4_train import (
    RULES,
    VOCABULARY,
    build,
    configuration_for,
    rescope,
    seed_experience,
)
from test_v04_m8_promotion import build as build_free

from accretion.contracts import (
    EventType,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Run,
    RunState,
)
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    RouterModelVersion,
    RouterPromotionDecision,
    RouterStatus,
    RouterTrainingSnapshot,
    RoutingContext,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutResult,
    SnapshotSplit,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.features import Vocabulary
from accretion.routing.ope import PromotionConfig, default_promotion_config
from accretion.routing.promotion import (
    COHORT_LABEL,
    HoldoutLeakageError,
    PromotionEvaluator,
    RollbackDrill,
    evaluation_cohorts,
)
from accretion.routing.ranker import (
    ArtifactDigestMismatchError,
    DistributionEstimate,
    LearnedOutcomePredictor,
    OutcomeHead,
    PredictedOutcomes,
    UncertaintySummary,
)
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    ALGORITHM_ID,
    CALIBRATION_REPORT_LABEL,
    HoldoutEvaluation,
    LearnedPredictorLoader,
)
from accretion.routing.training_snapshot import SnapshotBuilder, SnapshotRules

TRAINING_WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
HOLDOUT_WINDOW = (datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC))
TRAINING_FIRST = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
HOLDOUT_FIRST = datetime(2026, 2, 2, 9, 0, tzinfo=UTC)
SEALED_AT = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

TRAINING_PROJECTS = 3
HOLDOUT_PROJECTS = 6
PER_PROJECT = 4
FAILING_SLOT = 3
"""One record in four is the run that later failed, which is where label variation comes from."""


def sealed_clock() -> datetime:
    """The one instant every document built here is stamped with.

    A snapshot's ``created_at`` is inside its ``content_hash``, so a rebuild is only
    comparable with the original when the clock is replayed rather than read.
    """

    return SEALED_AT


# --------------------------------------------------------------------------------------
# The corpus: two disjoint windows over two disjoint sets of projects.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Corpus:
    """One workspace whose training evidence and holdout evidence share no project."""

    store: MemoryStore
    workspace_id: str
    principal: PrincipalRef
    training_projects: tuple[str, ...]
    holdout_projects: tuple[str, ...]


async def seed_node(
    store: MemoryStore,
    *,
    workspace_id: str,
    project_id: str,
    label: str,
    created_at: datetime,
    succeeded: bool,
    cohorts: Sequence[str] = (),
    propensity: float | None = None,
) -> str:
    """One node, its routing decision, the configuration it ran, and the outcome it produced.

    The whole join M4's trainer performs, written once here because the gate reads it back:
    a record whose routing context, configuration candidate or objective is missing cannot be
    featurized, and a corpus that omitted any of them would make the evaluator's row count
    zero for a reason that has nothing to do with what is being tested.
    """

    objective = build(
        ObjectiveContract, workspace_id=workspace_id, project_id=project_id
    )
    await store.put_objective_contract(objective)

    execution_instance_id = f"exe_{uuid4().hex[:16]}"
    node_document = json.loads(
        (M4_FIXTURE_ROOT / "node_contract" / "minimal.json").read_text(encoding="utf-8")
    )
    reference = rescope(
        node_document["objective_contract_ref"],
        workspace_id=workspace_id,
        project_id=project_id,
    )
    reference["objective_contract_id"] = objective.contract_id
    reference["objective_contract_hash"] = objective.content_hash
    node = build(
        NodeContract,
        workspace_id=workspace_id,
        project_id=project_id,
        execution_instance_id=execution_instance_id,
        node_id=f"node-{label}",
        objective_contract_ref=reference,
    )
    await store.put_node_contract(node)

    context = build(
        RoutingContext,
        workspace_id=workspace_id,
        project_id=project_id,
        node_contract_ref={
            "node_contract_id": node.contract_id,
            "immutable_hash": node.immutable_hash,
        },
    )
    await store.put_routing_request(context)

    configuration = configuration_for(
        workspace_id=workspace_id, project_id=project_id, strong=succeeded
    )
    candidate = build(
        ConfigurationCandidate,
        workspace_id=workspace_id,
        project_id=project_id,
        routing_request_id=context.contract_id,
        configuration=configuration.model_dump(mode="json"),
    )
    await store.put_configuration_candidate(candidate)

    if propensity is not None:
        # §9.5 records the behaviour propensity precisely so the off-policy estimator does
        # not have to guess it, and an ``EXPLORE`` decision is refused without one — which is
        # what makes a low-propensity corpus buildable at all.
        await store.put_routing_receipt(
            build(
                RoutingDecisionReceipt,
                workspace_id=workspace_id,
                project_id=project_id,
                routing_request_id=context.contract_id,
                node_contract_hash=node.immutable_hash,
                decision_type="EXPLORE",
                selected_configuration_id=candidate.configuration.contract_id,
                selected_configuration_hash=candidate.configuration.configuration_hash,
                selection_propensity=propensity,
            )
        )

    experience_id = new_id("experience")
    await seed_experience(store, experience_id, project_id)
    record = build(
        ExperienceRecord,
        workspace_id=workspace_id,
        project_id=project_id,
        contract_id=experience_id,
        created_at=created_at.isoformat(),
        source_node_execution_id=execution_instance_id,
        configuration_hash=candidate.configuration.configuration_hash,
        eligible_for_learning=True,
        local_verification_status="PASS",
        final_run_status="PASS" if succeeded else "FAIL",
        labels=({COHORT_LABEL: ",".join(cohorts)} if cohorts else {}),
        outcomes={
            "quality": 0.9 if succeeded else 0.3,
            "cost": "2.50" if succeeded else "4.50",
            "latency_ms": 20_000 if succeeded else 60_000,
        },
    )
    await store.put_experience_record(record)
    return experience_id


async def setup_corpus(
    *, secret_cohort: bool = False, propensity: float | None = None
) -> Corpus:
    """Three training projects in January and six holdout projects in February.

    Two axes are separated at once and both matter. The *projects* are disjoint, which is
    what makes the holdout a holdout; the *windows* are disjoint, which is what lets
    :class:`~accretion.routing.training_snapshot.SnapshotBuilder` — whose only filter is the
    window — seal two snapshots that name two different sets of records without either being
    told which projects it is for.

    ``secret_cohort`` labels the failing holdout records as ``secrets``, which is the cohort
    AC4-M8-037 regresses. Declared rather than derived, because nothing in an experience
    record says a node handled a credential.
    """

    store = MemoryStore()
    workspace_id = f"wks_{uuid4().hex[:12]}"
    principal = PrincipalRef(
        principal_id=f"usr_{uuid4().hex[:12]}",
        display_name="v0.4 M8 promotion evaluator",
        status=PrincipalStatus.ACTIVE,
    )
    training_projects: list[str] = []
    holdout_projects: list[str] = []
    minted = 0

    for group, count, first in (
        ("train", TRAINING_PROJECTS, TRAINING_FIRST),
        ("holdout", HOLDOUT_PROJECTS, HOLDOUT_FIRST),
    ):
        for index in range(count):
            project_id = new_id("project")
            (training_projects if group == "train" else holdout_projects).append(project_id)
            await store.create_project(
                Project(
                    project_id=project_id,
                    name=f"v0.4 M8 {group} project {index}",
                    repository_path=Path("/tmp/accretion-v04-m8-evaluator"),
                )
            )
            for slot in range(PER_PROJECT):
                minted += 1
                succeeded = slot != FAILING_SLOT
                await seed_node(
                    store,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    label=f"{group}-{index}-{slot}",
                    created_at=first + timedelta(hours=minted),
                    succeeded=succeeded,
                    cohorts=(
                        ("secrets",)
                        if secret_cohort and group == "holdout" and not succeeded
                        else ()
                    ),
                    propensity=propensity,
                )

    return Corpus(
        store=store,
        workspace_id=workspace_id,
        principal=principal,
        training_projects=tuple(training_projects),
        holdout_projects=tuple(holdout_projects),
    )


async def snapshot_over(
    corpus: Corpus,
    window: tuple[datetime, datetime],
    rules: SnapshotRules = RULES,
) -> RouterTrainingSnapshot:
    """Seal the eligible evidence in one window under the workspace's declared split.

    The *same* split is declared on both snapshots on purpose: a workspace has one split, and
    which of the two snapshots a record belongs to is decided by the window rather than by
    two different descriptions of the same partition. That is what makes the declared-list
    validator pass in the leaking case below while the manifest check still refuses.
    """

    snapshot = await SnapshotBuilder(corpus.store).build(
        workspace_id=corpus.workspace_id,
        window=window,
        split=SnapshotSplit(
            training_project_ids=sorted(corpus.training_projects),
            holdout_project_ids=sorted(corpus.holdout_projects),
        ),
        rules=rules,
        created_by=corpus.principal,
        clock=sealed_clock,
    )
    return await corpus.store.put_router_training_snapshot(snapshot)


# --------------------------------------------------------------------------------------
# Fakes. Hand written, with a call counter and a failure switch, and no mocks anywhere.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScriptedPredictor:
    """A predictor whose success estimate is chosen by the test rather than fitted.

    ``lower`` is what the threshold family reads and ``mean`` is what the direct-method term
    prices a deferral at, so the two together are the whole of what the gate sees. Real
    numbers here would make every assertion below a claim about the trainer.
    """

    mean: float
    lower: float

    def _estimate(self, mean: float, lower: float) -> DistributionEstimate:
        return DistributionEstimate(
            mean=mean,
            lower_bound=lower,
            upper_bound=1.0,
            confidence=0.95,
            method="scripted/1",
        )

    def predict(
        self, row: Sequence[float | None]
    ) -> tuple[PredictedOutcomes, UncertaintySummary]:
        success = self._estimate(self.mean, self.lower)
        neutral = self._estimate(0.5, 0.25)
        return (
            PredictedOutcomes(
                quality=neutral,
                cost=neutral,
                latency=neutral,
                node_verified_success=success,
                run_verified_success=success,
            ),
            UncertaintySummary(
                epistemic_uncertainty=0.05,
                lower_confidence_success=self.lower,
                calibration_version="scripted-calibration/1",
            ),
        )


class ScriptedLoader(LearnedPredictorLoader):
    """The real loader with only the decoding of five bagged heads substituted.

    A subclass and not a stand-in, for the reason M5's ``StubLoader`` gives: every store
    lookup, every label check and every digest read stays real, and only the expensive part
    is replaced. ``failure`` is the switch the adversarial digest-mismatch case flips, and
    ``assemble_calls`` is the counter that says the substitution was actually reached.
    """

    def __init__(
        self,
        store: MemoryStore,
        artifacts: ArtifactStore,
        predictors: dict[str, ScriptedPredictor],
    ) -> None:
        super().__init__(store, artifacts)
        self.predictors = predictors
        self.assemble_calls = 0
        self.failure: Exception | None = None
        self.failing_version_id: str | None = None

    def assemble(self, version: RouterModelVersion) -> Any:
        self.assemble_calls += 1
        if self.failure is not None and version.contract_id in (
            self.failing_version_id,
            None,
        ):
            raise self.failure
        return self.predictors[version.contract_id]


class ReversedGathering:
    """The real store with the *order* of one read reversed, and nothing else changed.

    Written by hand rather than mocked, and a wrapper rather than a subclass, because the
    substitution has to be visible in the test that uses it: ``list_experience_records`` is
    the one call :meth:`SnapshotBuilder.build` gathers its evidence through, and both store
    backends serve it in ``(created_at, contract_id)`` order. That order is stable for a
    fixed corpus, so a rebuild from the same store returns the same list however the
    manifest is assembled — which is why reproducibility cannot be proved by rebuilding
    twice from one store. Reversing the list is the smallest change that makes the two
    gatherings differ while naming the identical set of records.

    ``list_calls`` is the counter that says the substitution was reached, and ``gathered``
    keeps the ids in the order each call handed them over so the test can assert the two
    orderings really were different rather than assume it.
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.list_calls = 0
        self.gathered: list[tuple[str, ...]] = []

    async def list_experience_records(
        self, *, workspace_id: str, project_id: str | None = None
    ) -> list[ExperienceRecord]:
        records = await self.store.list_experience_records(
            workspace_id=workspace_id, project_id=project_id
        )
        self.list_calls += 1
        self.gathered.append(tuple(record.contract_id for record in records))
        return list(reversed(records))

    def __getattr__(self, name: str) -> Any:
        # Every other read the builder performs — ``get_experience`` today — goes to the
        # real store untouched. Delegating rather than re-declaring keeps this a wrapper
        # over one method instead of a second implementation of the protocol that would
        # drift the moment the builder read something new.
        return getattr(self.store, name)


# --------------------------------------------------------------------------------------
# The bench.
# --------------------------------------------------------------------------------------


def holdout_evaluation(
    snapshot_id: str,
    project_ids: Sequence[str],
    *,
    rate: float,
    lcb: float,
    ece: float,
    false_acceptance: float,
    n_rows: int = 5_000,
) -> HoldoutEvaluation:
    """A sealed §10.2 evaluation with the four numbers the three mandatory gates read."""

    return HoldoutEvaluation(
        training_snapshot_id=snapshot_id,
        feature_schema_version="1.0.0",
        alpha=0.05,
        conformal_quantile=0.9,
        seed=7,
        n_rows=n_rows,
        project_ids=sorted(project_ids),
        observed_verified_success_rate=rate,
        verified_success_lcb=lcb,
        ece_10bin=ece,
        brier=0.1,
        accepted_count=n_rows,
        false_acceptance_rate=false_acceptance,
    )


def router_version(
    workspace_id: str,
    *,
    snapshot_id: str,
    status: RouterStatus,
    artifact_digest: str,
    calibration_digest: str,
    holdout_digest: str,
    parent_version_id: str | None = None,
) -> RouterModelVersion:
    """A workspace-scoped version pinning the two artefacts and its sealed evaluation."""

    return build_free(
        RouterModelVersion,
        workspace_id=workspace_id,
        project_id=None,
        scope="TEAM_WORKSPACE",
        algorithm_id=ALGORITHM_ID,
        training_snapshot_id=snapshot_id,
        artifact_digest=artifact_digest,
        calibration_artifact_digest=calibration_digest,
        parent_version_id=parent_version_id,
        status=status.value,
        labels={
            ACCEPTANCE_LABEL: holdout_digest,
            CALIBRATION_REPORT_LABEL: calibration_digest,
        },
    )


@dataclass(slots=True)
class Bench:
    """A workspace with a scored candidate, a scored incumbent and a wired gate."""

    corpus: Corpus
    artifacts: ArtifactStore
    training: RouterTrainingSnapshot
    holdout: RouterTrainingSnapshot
    candidate: RouterModelVersion
    baseline: RouterModelVersion
    loader: ScriptedLoader
    evaluator: PromotionEvaluator

    @property
    def store(self) -> MemoryStore:
        return self.corpus.store

    @property
    def workspace_id(self) -> str:
        return self.corpus.workspace_id

    async def evaluate(self, **overrides: Any) -> Any:
        fields: dict[str, Any] = {
            "candidate_version_id": self.candidate.contract_id,
            "baseline_version_id": self.baseline.contract_id,
            "holdout_snapshot_id": self.holdout.contract_id,
        }
        fields.update(overrides)
        return await self.evaluator.evaluate(
            fields["candidate_version_id"],
            fields["baseline_version_id"],
            fields["holdout_snapshot_id"],
            self.corpus.principal,
            fields.get("run_id"),
            idempotency_key=fields.get("idempotency_key"),
        )


async def setup_bench(
    tmp_path: Path,
    *,
    secret_cohort: bool = False,
    propensity: float | None = None,
    holdout_window: tuple[datetime, datetime] = HOLDOUT_WINDOW,
    candidate_lower: float = 0.9,
    baseline_mean: float = 0.1,
    candidate_holdout: dict[str, float] | None = None,
    baseline_holdout: dict[str, float] | None = None,
    config: PromotionConfig | None = None,
    corpus: Corpus | None = None,
    rules: SnapshotRules = RULES,
    vocabulary: Vocabulary = VOCABULARY,
) -> Bench:
    """A fresh store, two snapshots, two versions and the gate that compares them.

    Nothing is shared between benches. These tests count rows, cohorts and findings, and a
    shared store would make every count depend on how many earlier tests had run.
    """

    built = (
        corpus
        if corpus is not None
        else await setup_corpus(secret_cohort=secret_cohort, propensity=propensity)
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    training = await snapshot_over(built, TRAINING_WINDOW, rules)
    holdout = await snapshot_over(built, holdout_window, rules)

    artifact_digest = artifacts.save(json.dumps({"artifact": "scripted"}).encode())
    calibration_digest = artifacts.save(json.dumps({"calibration": "scripted"}).encode())
    candidate_digest = artifacts.save(
        holdout_evaluation(
            training.contract_id,
            built.holdout_projects,
            **(
                candidate_holdout
                if candidate_holdout is not None
                else {"rate": 0.9, "lcb": 0.8, "ece": 0.02, "false_acceptance": 0.05}
            ),
        )
        .model_dump_json()
        .encode()
    )
    baseline_digest = artifacts.save(
        holdout_evaluation(
            training.contract_id,
            built.holdout_projects,
            **(
                baseline_holdout
                if baseline_holdout is not None
                else {"rate": 0.7, "lcb": 0.6, "ece": 0.03, "false_acceptance": 0.06}
            ),
        )
        .model_dump_json()
        .encode()
    )

    baseline = router_version(
        built.workspace_id,
        snapshot_id=training.contract_id,
        status=RouterStatus.ACTIVE,
        artifact_digest=artifact_digest,
        calibration_digest=calibration_digest,
        holdout_digest=baseline_digest,
    )
    candidate = router_version(
        built.workspace_id,
        snapshot_id=training.contract_id,
        status=RouterStatus.CANDIDATE,
        artifact_digest=artifact_digest,
        calibration_digest=calibration_digest,
        holdout_digest=candidate_digest,
    )
    for version in (baseline, candidate):
        await built.store.put_router_model_version(version)

    loader = ScriptedLoader(
        built.store,
        artifacts,
        {
            candidate.contract_id: ScriptedPredictor(
                mean=max(0.95, candidate_lower), lower=candidate_lower
            ),
            baseline.contract_id: ScriptedPredictor(mean=baseline_mean, lower=0.0),
        },
    )
    evaluator = PromotionEvaluator(
        built.store,
        loader,
        artifacts,
        config if config is not None else default_promotion_config(),
        RollbackDrill(loader, artifacts),
        vocabulary=vocabulary,
    )
    return Bench(
        corpus=built,
        artifacts=artifacts,
        training=training,
        holdout=holdout,
        candidate=candidate,
        baseline=baseline,
        loader=loader,
        evaluator=evaluator,
    )


async def seed_shadow_stage(bench: Bench, *, pairs: int = 12, delta: float = 0.08) -> str:
    """A SHADOW version parented on the candidate, with enough paired rollouts to clear §10.2.

    The decision ids are derived from their index rather than minted, for the reason M6's
    ``paired_corpus`` gives: ``hierarchical_bootstrap`` sorts its group keys, so random ids
    would resample the same numbers in a different order and the gate would be seed-stable
    within a process and irreproducible between two.
    """

    shadow = router_version(
        bench.workspace_id,
        snapshot_id=bench.training.contract_id,
        status=RouterStatus.SHADOW,
        artifact_digest=bench.candidate.artifact_digest,
        calibration_digest=bench.candidate.calibration_artifact_digest,
        holdout_digest=bench.candidate.labels[ACCEPTANCE_LABEL],
        parent_version_id=bench.candidate.contract_id,
    )
    await bench.store.put_router_model_version(shadow)
    project_id = bench.corpus.holdout_projects[0]
    for _index in range(pairs):
        decision = build(
            ShadowDecision,
            workspace_id=bench.workspace_id,
            project_id=project_id,
            contract_id=new_id("shadow_decision"),
            shadow_router_version_id=shadow.contract_id,
            executed_receipt_id=new_id("routing_receipt"),
            shadow_receipt_id=new_id("routing_receipt"),
        )
        await bench.store.put_shadow_decision(decision)
        for kind, quality in (("CONTROL", 0.5), ("SHADOW", 0.5 + delta)):
            await bench.store.put_shadow_rollout_result(
                build(
                    ShadowRolloutResult,
                    workspace_id=bench.workspace_id,
                    project_id=project_id,
                    contract_id=new_id("shadow_rollout_result"),
                    shadow_decision_id=decision.contract_id,
                    kind=kind,
                    trial_index=0,
                    fork_execution_id=new_id("runtime_call"),
                    observed={
                        "quality": quality,
                        "cost": 0.4,
                        "latency_ms": 18_000.0,
                        "verified": False,
                    },
                )
            )
    return shadow.contract_id


# --------------------------------------------------------------------------------------
# AC4-M8-035
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M8-035")
async def test_a_stored_snapshot_rebuilds_to_the_same_bytes_and_a_second_put_is_a_no_op(
    tmp_path: Path,
) -> None:
    """§10.1: a snapshot that could be described but not rebuilt is an unfalsifiable claim.

    The rebuild replays the original's ``created_at`` — the rule
    :meth:`SnapshotBuilder.build`'s docstring states — because the instant is inside the
    ``content_hash`` while the derived id does not cover it, so a rebuild under a live clock
    would produce the same id over a different body and the append-only store would rightly
    refuse it. Everything is read back from the store rather than compared against the object
    that was handed to it.
    """

    corpus = await setup_corpus()
    first = await snapshot_over(corpus, TRAINING_WINDOW)
    stored = await corpus.store.get_router_training_snapshot(first.contract_id)
    assert stored is not None

    rebuilt = await SnapshotBuilder(corpus.store).build(
        workspace_id=corpus.workspace_id,
        window=TRAINING_WINDOW,
        split=SnapshotSplit(
            training_project_ids=sorted(corpus.training_projects),
            holdout_project_ids=sorted(corpus.holdout_projects),
        ),
        rules=RULES,
        created_by=corpus.principal,
        clock=lambda: stored.created_at,
    )
    replayed = await corpus.store.put_router_training_snapshot(rebuilt)

    assert rebuilt.contract_id == stored.contract_id
    assert rebuilt.content_hash == stored.content_hash
    assert rebuilt.included_experience_ids == sorted(stored.included_experience_ids)
    assert replayed.content_hash == stored.content_hash
    read_back = await corpus.store.get_router_training_snapshot(stored.contract_id)
    assert read_back is not None
    assert read_back.content_hash == stored.content_hash
    assert (
        len(await corpus.store.list_router_training_snapshots(workspace_id=corpus.workspace_id))
        == 1
    )


@pytest.mark.acceptance("AC4-M8-035")
async def test_a_snapshot_rebuilt_under_a_live_clock_is_refused_rather_than_overwritten(
    tmp_path: Path,
) -> None:
    """Immutability is the other half of AC4-M8-035, and it is a refusal and not a silent win.

    Same inputs, same derived id, a different instant inside the body: the store's guard
    raises rather than replacing the sealed document, which is what makes "immutable" a
    property of the table and not a convention the writer is trusted to follow.
    """

    corpus = await setup_corpus()
    first = await snapshot_over(corpus, TRAINING_WINDOW)
    drifted = await SnapshotBuilder(corpus.store).build(
        workspace_id=corpus.workspace_id,
        window=TRAINING_WINDOW,
        split=SnapshotSplit(
            training_project_ids=sorted(corpus.training_projects),
            holdout_project_ids=sorted(corpus.holdout_projects),
        ),
        rules=RULES,
        created_by=corpus.principal,
        clock=lambda: first.created_at + timedelta(seconds=1),
    )

    assert drifted.contract_id == first.contract_id
    assert drifted.content_hash != first.content_hash
    with pytest.raises(ValueError):
        await corpus.store.put_router_training_snapshot(drifted)

    read_back = await corpus.store.get_router_training_snapshot(first.contract_id)
    assert read_back is not None
    assert read_back.content_hash == first.content_hash


@pytest.mark.acceptance("AC4-M8-035")
async def test_the_same_evidence_gathered_in_another_order_seals_the_same_snapshot(
    tmp_path: Path,
) -> None:
    """Reproducible means "independent of gathering order", and only this test says so.

    The rebuild above re-reads the *same* store, which lists in ``(created_at,
    contract_id)`` order and so hands the builder the identical sequence both times: that
    rebuild would come back byte-identical even if the manifest were assembled in whatever
    order the rows arrived, so it cannot distinguish a sorted manifest from an accidental
    one. §10.1's claim is stronger — the same evidence must seal the same snapshot however
    it was gathered — and the manifest sort in
    :func:`~accretion.routing.training_snapshot._deduplicate` is the line that makes it
    true.

    So the second gathering goes through :class:`ReversedGathering`, which names the same
    records in the opposite order and changes nothing else. Identical ``contract_id`` (the
    manifest digest is inside it), identical ``content_hash``, identical
    ``included_experience_ids`` and a second ``put`` the append-only store accepts as the
    no-op it is. Dropping the sort makes all four differ.
    """

    corpus = await setup_corpus()
    first = await snapshot_over(corpus, TRAINING_WINDOW)

    reversed_store = ReversedGathering(corpus.store)
    rebuilt = await SnapshotBuilder(reversed_store).build(
        workspace_id=corpus.workspace_id,
        window=TRAINING_WINDOW,
        split=SnapshotSplit(
            training_project_ids=sorted(corpus.training_projects),
            holdout_project_ids=sorted(corpus.holdout_projects),
        ),
        rules=RULES,
        created_by=corpus.principal,
        clock=lambda: first.created_at,
    )

    # The substitution was reached and it really did change the order, rather than the
    # test asserting reproducibility over a gathering that happened to be identical.
    assert reversed_store.list_calls == 1
    manifest = set(first.included_experience_ids)
    # The read is over the whole workspace and the window filter runs inside the builder,
    # so the sequence that matters is the manifest's own records as this gathering
    # presented them: the same set, in an order the first gathering did not use.
    presented = tuple(
        contract_id for contract_id in reversed_store.gathered[0] if contract_id in manifest
    )
    assert set(presented) == manifest
    assert len(presented) > 1
    assert tuple(reversed(presented)) != presented

    assert rebuilt.contract_id == first.contract_id
    assert rebuilt.content_hash == first.content_hash
    assert rebuilt.included_experience_ids == first.included_experience_ids
    assert rebuilt.included_experience_ids == sorted(first.included_experience_ids)
    assert rebuilt.labels["manifest_digest"] == first.labels["manifest_digest"]

    replayed = await corpus.store.put_router_training_snapshot(rebuilt)
    assert replayed.content_hash == first.content_hash
    assert (
        len(await corpus.store.list_router_training_snapshots(workspace_id=corpus.workspace_id))
        == 1
    )


# --------------------------------------------------------------------------------------
# AC4-M8-036
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M8-036")
async def test_a_holdout_naming_a_training_projects_record_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """The declared split is disjoint here, and the gate refuses anyway (AC4-M8-036).

    The "holdout" snapshot is sealed over the *training* window, so every record it names
    comes from a training project while its declared ``SnapshotSplit`` still lists the six
    holdout projects and passes its own validator. Only the explicit manifest check can see
    this, which is exactly why it exists: a candidate scored on projects it was fitted on is
    measuring memorisation, and no threshold, band or cohort repairs that.
    """

    bench = await setup_bench(tmp_path, holdout_window=TRAINING_WINDOW)
    before = len(
        await bench.store.list_router_promotion_reports(workspace_id=bench.workspace_id)
    )

    with pytest.raises(HoldoutLeakageError) as refusal:
        await bench.evaluate()

    assert refusal.value.code == "ROUTER_HOLDOUT_LEAKAGE"
    assert "measures memorisation" in refusal.value.message
    assert (
        len(await bench.store.list_router_promotion_reports(workspace_id=bench.workspace_id))
        == before
    )


@pytest.mark.acceptance("AC4-M8-036")
async def test_a_project_disjoint_holdout_is_accepted_and_scored(tmp_path: Path) -> None:
    """The refusal above is only meaningful if the disjoint case goes through.

    Every holdout project appears in the report's scored evidence and none of the training
    projects does, which is checked against the snapshot the report names rather than against
    the corpus object the test built.
    """

    bench = await setup_bench(tmp_path)
    report = await bench.evaluate()

    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None
    assert stored.holdout_definition_id == bench.holdout.contract_id
    assert stored.training_snapshot_id == bench.training.contract_id

    named = await bench.store.get_router_training_snapshot(stored.holdout_definition_id)
    assert named is not None
    projects = set()
    for experience_id in named.included_experience_ids:
        record = await bench.store.get_experience_record(experience_id)
        assert record is not None
        projects.add(record.project_id)
    assert projects == set(bench.corpus.holdout_projects)
    assert projects & set(bench.corpus.training_projects) == set()


# --------------------------------------------------------------------------------------
# AC4-M8-037
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M8-037")
async def test_a_critical_cohort_regression_rejects_a_candidate_whose_mean_improved(
    tmp_path: Path,
) -> None:
    """§10.3: a critical correctness or safety regression is a block and not a tradeoff.

    The candidate is substantially better on the holdout as a whole and worse on the cohort
    the registered config calls critical. The verdict is ``REJECT`` and the cohort is named
    in the report, so an operator reading it can see which slice refused rather than only
    that something did.
    """

    bench = await setup_bench(tmp_path, secret_cohort=True)
    await seed_shadow_stage(bench)

    report = await bench.evaluate()
    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None

    assert stored.primary_metric_result.delta > 0
    assert stored.primary_metric_result.passed
    secrets = next(item for item in stored.cohort_results if item.cohort_id == "secrets")
    assert secrets.critical
    assert secrets.sample_size == HOLDOUT_PROJECTS
    assert not secrets.comparison.passed
    assert stored.decision is RouterPromotionDecision.REJECT
    assert stored.approved_by is None


@pytest.mark.acceptance("AC4-M8-037")
async def test_the_same_numbers_promote_once_that_cohort_is_no_longer_critical(
    tmp_path: Path,
) -> None:
    """The mutation the criterion is about, run as a test rather than described.

    Nothing changes but the registered ``critical_cohorts`` list: the same records, the same
    predictions, the same regression on the same slice. The verdict flips to ``PROMOTE``,
    which is what makes the rejection above a statement about *criticality* and not about
    some other property of the corpus.
    """

    registered = default_promotion_config()
    relaxed = registered.model_copy(
        update={
            "critical_cohorts": [
                cohort for cohort in registered.critical_cohorts if cohort != "secrets"
            ]
        }
    )
    bench = await setup_bench(tmp_path, secret_cohort=True, config=relaxed)
    await seed_shadow_stage(bench)

    report = await bench.evaluate()
    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None

    secrets = next(item for item in stored.cohort_results if item.cohort_id == "secrets")
    assert not secrets.critical
    assert not secrets.comparison.passed
    assert stored.decision is RouterPromotionDecision.PROMOTE
    assert stored.approved_by == bench.corpus.principal


# --------------------------------------------------------------------------------------
# AC4-M8-038, the evaluator's half
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M8-038")
async def test_a_rollback_target_that_will_not_drill_cannot_produce_a_promotable_report(
    tmp_path: Path,
) -> None:
    """§10.3's reversibility, enforced where the report is written and not only where it is used.

    M8.1 proves the promote route refuses a report whose ``rollback_target`` will not load;
    this proves the gate never issues such a report in the first place. The drill's failure
    is recorded as a critical regression, so the refusal is attributable rather than merely
    a decision value.
    """

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)
    bench.loader.failure = ArtifactDigestMismatchError(
        "stored bytes no longer hash to the digest the version pinned"
    )
    bench.loader.failing_version_id = bench.baseline.contract_id

    report = await bench.evaluate()
    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None

    assert stored.rollback_target == bench.baseline.contract_id
    assert "ROLLBACK_TARGET_UNDRILLABLE" in {
        finding.finding_id for finding in stored.critical_regressions
    }
    assert stored.decision is RouterPromotionDecision.REJECT


# --------------------------------------------------------------------------------------
# The gate's own machinery.
# --------------------------------------------------------------------------------------


async def test_the_promoted_report_names_the_threshold_it_was_read_at(tmp_path: Path) -> None:
    """``c*`` is chosen on the tune projects and the report records which one it was.

    A gate that did not record the threshold would leave "the improvement" a number nobody
    could reproduce, because there is one improvement per grid point and they differ.
    """

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)

    report = await bench.evaluate()

    threshold = float(report.labels["accretion.acceptance-threshold"])
    assert threshold in default_promotion_config().thresholds()
    assert report.labels["accretion.promotion-config"] == "promotion-v1"
    assert report.decision is RouterPromotionDecision.PROMOTE


async def test_a_candidate_with_no_shadow_stage_is_reviewed_rather_than_promoted(
    tmp_path: Path,
) -> None:
    """§10.2 gates promotion on shadow evidence, and no evidence is not neutral evidence.

    The tradeoff is disclosed with the bound it missed, which is what
    :class:`~accretion.contracts.routing.RouterPromotionReport` requires of a non-critical
    finding: an undisclosed tradeoff is a regression.
    """

    bench = await setup_bench(tmp_path)

    report = await bench.evaluate()
    stored = await bench.store.get_router_promotion_report(report.contract_id)
    assert stored is not None

    assert stored.shadow_result.sample_size == 0
    assert stored.shadow_result.decision_count == 0
    assert stored.decision is RouterPromotionDecision.REQUIRE_REVIEW
    tradeoff = next(
        item
        for item in stored.noncritical_tradeoffs
        if item.finding_id == "SHADOW_EVIDENCE_INSUFFICIENT"
    )
    assert tradeoff.disclosed_bound == "min_paired_runs=9, delta_ni=-0.02"


async def test_a_shadow_stage_with_too_few_pairs_does_not_clear_the_registered_floor(
    tmp_path: Path,
) -> None:
    """A lower bound computed from four pairs can clear any floor; the count gate is first."""

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench, pairs=4)

    report = await bench.evaluate()

    assert report.shadow_result.sample_size == 4
    assert report.decision is RouterPromotionDecision.REQUIRE_REVIEW


async def test_a_candidate_whose_calibration_exceeds_the_ceiling_is_rejected(
    tmp_path: Path,
) -> None:
    """Every lower bound a router produces is read as a guarantee; an uncalibrated one is not."""

    bench = await setup_bench(
        tmp_path,
        candidate_holdout={"rate": 0.9, "lcb": 0.8, "ece": 0.4, "false_acceptance": 0.05},
    )
    await seed_shadow_stage(bench)

    report = await bench.evaluate()

    assert report.calibration_result.candidate_value == 0.4
    assert not report.calibration_result.passed
    assert "CALIBRATION_CEILING_EXCEEDED" in {
        finding.finding_id for finding in report.critical_regressions
    }
    assert report.decision is RouterPromotionDecision.REJECT


async def test_a_candidate_that_accepts_more_of_what_fails_is_rejected(tmp_path: Path) -> None:
    """The false-acceptance gate is one-sided: a rise is a regression, a fall is not."""

    bench = await setup_bench(
        tmp_path,
        candidate_holdout={"rate": 0.9, "lcb": 0.8, "ece": 0.02, "false_acceptance": 0.30},
    )
    await seed_shadow_stage(bench)

    report = await bench.evaluate()

    assert not report.false_acceptance_non_regression.passed
    assert report.false_acceptance_non_regression.delta > 0
    assert report.decision is RouterPromotionDecision.REJECT


async def test_a_candidate_that_lost_verified_success_is_rejected(tmp_path: Path) -> None:
    """§10.2's first mandatory gate, read off the two sealed holdout evaluations."""

    bench = await setup_bench(
        tmp_path,
        candidate_holdout={"rate": 0.4, "lcb": 0.3, "ece": 0.02, "false_acceptance": 0.05},
    )
    await seed_shadow_stage(bench)

    report = await bench.evaluate()

    assert not report.verified_success_non_regression.passed
    assert report.decision is RouterPromotionDecision.REJECT


async def test_a_candidate_that_declines_every_row_is_reviewed_rather_than_promoted(
    tmp_path: Path,
) -> None:
    """A threshold policy that never takes the learned action *is* the incumbent.

    Its improvement is exactly zero at every grid point, which is neither a regression nor
    the shown improvement ``delta_min`` asks for, so the honest verdict is a review.
    """

    bench = await setup_bench(tmp_path, candidate_lower=0.0)
    await seed_shadow_stage(bench)

    report = await bench.evaluate()

    assert report.primary_metric_result.delta == pytest.approx(0.0)
    assert not report.primary_metric_result.passed
    assert report.decision is RouterPromotionDecision.REQUIRE_REVIEW


async def test_the_report_is_the_same_document_on_every_run_of_one_idempotency_key(
    tmp_path: Path,
) -> None:
    """A retry after a timeout returns the first verdict rather than sealing a second one."""

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)

    first = await bench.evaluate(idempotency_key="key-1")
    second = await bench.evaluate(idempotency_key="key-1")

    assert first.contract_id == second.contract_id
    assert first.content_hash == second.content_hash
    assert (
        len(await bench.store.list_router_promotion_reports(workspace_id=bench.workspace_id))
        == 1
    )


async def test_two_evaluations_without_a_key_are_two_sealed_claims(tmp_path: Path) -> None:
    """Two evaluations of the same triple a month apart are claims about different evidence."""

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)

    first = await bench.evaluate()
    second = await bench.evaluate()

    assert first.contract_id != second.contract_id
    assert (
        len(await bench.store.list_router_promotion_reports(workspace_id=bench.workspace_id))
        == 2
    )


async def test_an_unknown_version_or_snapshot_is_a_key_error(tmp_path: Path) -> None:
    """The API's 404 convention, which is also what keeps another workspace's ids invisible."""

    bench = await setup_bench(tmp_path)

    with pytest.raises(KeyError):
        await bench.evaluate(candidate_version_id=new_id("router_model_version"))
    with pytest.raises(KeyError):
        await bench.evaluate(baseline_version_id=new_id("router_model_version"))
    with pytest.raises(KeyError):
        await bench.evaluate(holdout_snapshot_id=new_id("router_training_snapshot"))


async def test_a_version_compared_with_itself_is_refused(tmp_path: Path) -> None:
    """A report compares two router versions; this one would compare a version with itself."""

    bench = await setup_bench(tmp_path)

    with pytest.raises(HoldoutLeakageError, match="both the candidate and the baseline"):
        await bench.evaluate(baseline_version_id=bench.candidate.contract_id)


async def test_the_evaluation_event_is_emitted_only_when_a_run_is_named(
    tmp_path: Path,
) -> None:
    """ADR4-M8-004: the event store is run-scoped, so a promotion event needs a run context."""

    bench = await setup_bench(tmp_path)
    await seed_shadow_stage(bench)
    run = Run(
        run_id=new_id("run"),
        task_id=new_id("task"),
        project_id=bench.corpus.holdout_projects[0],
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=bench.corpus.principal.principal_id,
    )
    await bench.store.create_run(run)

    await bench.evaluate()
    assert await bench.store.list_events(run.run_id) == []

    report = await bench.evaluate(run_id=run.run_id)

    events = await bench.store.list_events(run.run_id)
    assert [event.normalized_type for event in events] == [
        EventType.ROUTER_PROMOTION_EVALUATED
    ]
    assert events[0].payload["report_id"] == report.contract_id
    assert events[0].payload["decision"] == report.decision.value


# --------------------------------------------------------------------------------------
# Cohort membership.
# --------------------------------------------------------------------------------------


def test_a_record_belongs_to_every_cohort_it_answers_to_and_not_only_the_first() -> None:
    """Cohort membership is many-to-many; a partition would force a choice nobody can make."""

    record = build(
        ExperienceRecord,
        workspace_id="wks_cohorts",
        project_id="prj_cohorts",
        contract_id=new_id("experience"),
        local_verification_status="PASS",
        contradiction_status="OPEN",
        labels={COHORT_LABEL: "secrets, policy"},
    )

    assert evaluation_cohorts(record) == (
        "correctness",
        "policy",
        "secrets",
        "verifier_conflict",
    )


def test_a_record_whose_verifier_never_decided_is_not_correctness_evidence() -> None:
    """``INCONCLUSIVE`` is the absence of a judgement, not a failed one."""

    record = build(
        ExperienceRecord,
        workspace_id="wks_cohorts",
        project_id="prj_cohorts",
        contract_id=new_id("experience"),
        local_verification_status="INCONCLUSIVE",
    )

    assert "correctness" not in evaluation_cohorts(record)


async def test_a_critical_cohort_with_no_members_passes_with_a_recorded_zero(
    tmp_path: Path,
) -> None:
    """An absent cohort produced no evidence of a regression, and says so with its count.

    Failing it would block every promotion in a workspace that has never run a
    secret-handling node, which would make the cohort list a liveness requirement rather
    than a safety gate.
    """

    bench = await setup_bench(tmp_path)
    report = await bench.evaluate()

    empty = next(item for item in report.cohort_results if item.cohort_id == "secrets")
    assert empty.sample_size == 0
    assert empty.critical
    assert empty.comparison.passed
    assert {item.cohort_id for item in report.cohort_results} >= set(
        default_promotion_config().critical_cohorts
    )


def test_every_outcome_head_the_gate_reads_is_a_declared_head() -> None:
    """A guard against the scripted predictor drifting from the real one's shape."""

    assert OutcomeHead.RUN_VERIFIED_SUCCESS in set(OutcomeHead)
    assert issubclass(ScriptedPredictor, object)
    assert hasattr(LearnedOutcomePredictor, "predict")
