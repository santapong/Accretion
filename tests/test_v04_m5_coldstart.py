"""AC4-M5-021, the cold-start end-to-end, and the four §15.1 rungs the scorer degrades down.

**The criterion.** "Cross-domain evidence cannot directly enable live routing" is a negative
claim about an unbounded input, so the first test below is a seeded property over five
hundred draws rather than an example. Each draw gives the scorer a candidate with *no*
in-domain history and an arbitrary amount of out-of-domain history at an arbitrary observed
success rate, and asserts three things: the lower confidence bound is bit-identical to the
one produced with no cross-domain evidence at all, the predicted mean moved by at most the
:data:`~accretion.routing.coldstart.CROSS_DOMAIN_CAP`, and the decision that follows is a
fallback or a request for review — never an exploit. The §9.5 safe set is defined on the
bound, so those three together are the criterion: nothing about the volume of cross-domain
evidence can move a candidate into the set.

The draw is deliberately hostile in two ways. A project adapter is installed, with a large
bias, so that the adapter's serving-time influence — ``n_project / (n_project + k)`` — is the
second route by which a mis-scoped count could reach the bound; and the out-of-domain rate
is drawn from the extremes, so that a cap applied to the wrong quantity or at the wrong size
shows up as a bound that moved.

**The end-to-end.** §18.5's first scenario is a new project on a workspace that *has* a
promoted router. The router is trained once per session, from the committed benchmark corpus
in ``tests/router_corpus_generator.py``, and installed as the workspace's ACTIVE version. A
fresh project then routes with no evidence and no adapter, and what the receipt has to say is
that the workspace model decided, that there was no adapter, and that the decision was a
fallback — not that a project adapter it never had agreed with it.

**The ladders.** §15.1 gives a different, weaker answer for each input the router can lose,
and each of them is a way to end up routing deterministically while a receipt claims
otherwise. Three of them are exercised here against the real loader: a version that cannot be
assembled, a retriever that raises, and a snapshot whose vocabulary digest does not match the
table the scorer featurizes under.

There is no ``conftest.py``. The corpus builders are the M4 training module's, imported and
not edited; the routing stack is the M2 service module's, imported and not edited; the loader
fake below is hand written with a call counter and a failure switch.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from router_corpus_generator import CANDIDATES as CORPUS_CANDIDATES
from router_corpus_generator import build as build_corpus
from test_v04_m2_service import _routable_execution
from test_v04_m4_train import (
    FIRST_RECORD_AT,
    FIXTURE_ROOT,
    TEST_CONFIG,
    WINDOW,
    configuration_for,
    frozen_clock,
    rescope,
    seed_experience,
)
from test_v04_m4_train import (
    build as sealed,
)

from accretion.contracts import PrincipalRef, PrincipalStatus, Project
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ContractSignature,
    DecisionType,
    DistributionEstimate,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    PredictedOutcomes,
    RouterModelVersion,
    RouterStatus,
    RouterTrainingSnapshot,
    RoutingContext,
    UncertaintySummary,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.adapter import AdapterArtifact, ProjectAdapter
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.coldstart import (
    CROSS_DOMAIN_CAP,
    VOCABULARY,
    VOCABULARY_DIGEST_LABEL,
    ColdStartScorer,
)
from accretion.routing.protocols import RoutingMode
from accretion.routing.ranker import ArtifactDigestMismatchError
from accretion.routing.selector import DeterministicSelector
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.stages import (
    ADAPTER_UNAVAILABLE,
    DEGRADED_LABEL,
    EVIDENCE_UNAVAILABLE,
    VOCABULARY_MISMATCH,
    WORKSPACE_MODEL_UNAVAILABLE,
    ActiveVersions,
    node_signature,
)
from accretion.routing.train import (
    LearnedPredictorLoader,
    RouterNotEvaluatedError,
    RouterTrainingService,
)
from accretion.routing.training_snapshot import SnapshotRules

SEED = 20260906
DRAWS = 500
FLOOR = 0.7
"""The verified-success floor the property test gates on. Every draw's bound is below it."""

OQ_408_CAP = 0.15
"""OQ-408's cap, written out rather than imported.

Importing :data:`~accretion.routing.coldstart.CROSS_DOMAIN_CAP` would make the assertion
self-referential: raising the cap in the module would raise the bound the test checks
against, and the one mutation this test exists to catch would pass. The equality below pins
the module's constant to this literal, so the two can only agree by being changed together
and deliberately.
"""

CROSS_DOMAIN_POOL = 40
"""How many out-of-domain records the property test may draw from, of each polarity."""

STRONG_CANDIDATES = frozenset(
    entry["candidate_id"]
    for entry in sorted(
        CORPUS_CANDIDATES, key=lambda item: -float(item["predicted_success"])
    )[:2]
)
"""The corpus's two most capable configurations, used as its ``strong`` model tokens."""


# --------------------------------------------------------------------------------------
# One trained, promoted workspace router, built once per session from the pinned corpus.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LearnedWorkspace:
    """A workspace with enough resolved evidence to have fitted a router, and the artefacts."""

    store: MemoryStore
    artifacts: ArtifactStore
    workspace_id: str
    principal: PrincipalRef
    version: RouterModelVersion
    snapshot: RouterTrainingSnapshot


async def setup_learned_workspace(artifacts: ArtifactStore) -> LearnedWorkspace:
    """Project the benchmark corpus into experience records and fit one router on them.

    The corpus is ``tests/router_corpus_generator.py``'s: twelve projects, three nodes each,
    two trials per node. Its ``verified`` flag becomes the record's ``final_run_status`` and
    its quality/cost/latency become the outcomes, so the labels the router learns are the
    ones a committed, regenerable, seeded file already fixes rather than ones invented here.

    The snapshot is cut under :meth:`SnapshotRules.over` with no arguments, which means the
    *empty* vocabulary — the same table :data:`~accretion.routing.coldstart.VOCABULARY`
    featurizes under. That is what makes the version installable without tripping the
    vocabulary rung, and the mismatch test below moves the digest deliberately.
    """

    corpus = build_corpus()
    tasks = corpus["tasks.v1"]["tasks"]
    traces_by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace in corpus["replay-traces.v1"]["traces"]:
        key = (str(trace["task_id"]), str(trace["candidate_id"]))
        traces_by_cell.setdefault(key, []).append(trace)

    store = MemoryStore()
    workspace_id = f"wks_{uuid4().hex[:12]}"
    principal = PrincipalRef(
        principal_id=f"usr_{uuid4().hex[:12]}",
        display_name="v0.4 M5 cold start",
        status=PrincipalStatus.ACTIVE,
    )
    minted = 0
    for corpus_project in sorted({str(task["project_id"]) for task in tasks}):
        project_id = new_id("project")
        await store.create_project(
            Project(
                project_id=project_id,
                name=corpus_project,
                repository_path=Path("/tmp/accretion-v04-m5-coldstart"),
            )
        )
        objective = sealed(
            ObjectiveContract, workspace_id=workspace_id, project_id=project_id
        )
        await store.put_objective_contract(objective)

        for task in [item for item in tasks if item["project_id"] == corpus_project]:
            chosen = str(task["planner_choice"])
            for trace in traces_by_cell[(str(task["task_id"]), chosen)]:
                minted += 1
                execution_instance_id = f"exe_{uuid4().hex[:16]}"
                node_document = sealed(
                    NodeContract,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    execution_instance_id=execution_instance_id,
                    node_id=f"{task['task_id']}-t{trace['trial']}",
                    objective_contract_ref=_objective_ref(
                        objective, workspace_id=workspace_id, project_id=project_id
                    ),
                )
                await store.put_node_contract(node_document)
                context = sealed(
                    RoutingContext,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    node_contract_ref={
                        "node_contract_id": node_document.contract_id,
                        "immutable_hash": node_document.immutable_hash,
                    },
                )
                await store.put_routing_request(context)
                configuration = configuration_for(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    strong=chosen in STRONG_CANDIDATES,
                )
                candidate = sealed(
                    ConfigurationCandidate,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    routing_request_id=context.contract_id,
                    configuration=configuration.model_dump(mode="json"),
                )
                await store.put_configuration_candidate(candidate)

                experience_id = new_id("experience")
                await seed_experience(store, experience_id, project_id)
                await store.put_experience_record(
                    sealed(
                        ExperienceRecord,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        contract_id=experience_id,
                        created_at=(
                            FIRST_RECORD_AT + timedelta(minutes=minted)
                        ).isoformat(),
                        source_node_execution_id=execution_instance_id,
                        configuration_hash=candidate.configuration.configuration_hash,
                        eligible_for_learning=True,
                        local_verification_status="PASS",
                        final_run_status="PASS" if trace["verified"] else "FAIL",
                        outcomes={
                            "quality": float(trace["quality"]),
                            "cost": str(trace["cost"]),
                            "latency_ms": int(trace["latency_ms"]),
                        },
                    )
                )

    trained = await RouterTrainingService(
        store, artifacts, clock=frozen_clock, config=TEST_CONFIG
    ).train_candidate(
        workspace_id=workspace_id,
        window=WINDOW,
        seed=SEED,
        created_by=principal,
        rules=SnapshotRules.over(),
    )
    return LearnedWorkspace(
        store=store,
        artifacts=artifacts,
        workspace_id=workspace_id,
        principal=principal,
        version=trained.version,
        snapshot=trained.snapshot,
    )


def _objective_ref(
    objective: ObjectiveContract, *, workspace_id: str, project_id: str
) -> dict[str, Any]:
    """The committed node contract's own objective reference, re-pointed at ``objective``.

    An :class:`~accretion.contracts.routing.ObjectiveContractRef` is a sealed document in its
    own right, not a two-field pointer, so it is taken from the fixture and edited rather
    than assembled here — a hand-built one would drift from the contract the moment a field
    moved, and the drift would surface as an unroutable node rather than as a schema error.
    """

    document = json.loads(
        (FIXTURE_ROOT / "node_contract" / "minimal.json").read_text(encoding="utf-8")
    )
    reference = rescope(
        document["objective_contract_ref"],
        workspace_id=workspace_id,
        project_id=project_id,
    )
    reference["objective_contract_id"] = objective.contract_id
    reference["objective_contract_hash"] = objective.content_hash
    return reference


_LEARNED: LearnedWorkspace | None = None


async def learned_once(tmp_path_factory: pytest.TempPathFactory) -> LearnedWorkspace:
    """Fit the shared router once. Fitting five bagged heads is the expensive thing here."""

    global _LEARNED
    if _LEARNED is None:
        _LEARNED = await setup_learned_workspace(
            ArtifactStore(tmp_path_factory.mktemp("m5-router-artifacts"))
        )
    return _LEARNED


async def install_router(
    store: MemoryStore,
    workspace_id: str,
    learned: LearnedWorkspace,
    *,
    status: RouterStatus = RouterStatus.ACTIVE,
    vocabulary_digest: str | None = None,
) -> RouterModelVersion:
    """Re-tenant the trained snapshot and version into another workspace's store.

    The artefact bytes are content addressed, so only the two documents move; the digests
    they pin resolve in the same :class:`~accretion.routing.artifacts.ArtifactStore`. Copying
    rather than re-training is what keeps every test in this module inside its budget while
    still routing through the real :class:`~accretion.routing.train.LearnedPredictorLoader`.
    """

    snapshot_payload = learned.snapshot.model_dump(mode="python")
    snapshot_payload.update(workspace_id=workspace_id, content_hash="")
    if vocabulary_digest is not None:
        snapshot_payload["labels"] = {
            **learned.snapshot.labels,
            VOCABULARY_DIGEST_LABEL: vocabulary_digest,
        }
    snapshot = RouterTrainingSnapshot.model_validate(snapshot_payload)
    await store.put_router_training_snapshot(snapshot)

    version_payload = learned.version.model_dump(mode="python")
    version_payload.update(
        contract_id=new_id("router_model_version"),
        workspace_id=workspace_id,
        training_snapshot_id=snapshot.contract_id,
        status=status,
        content_hash="",
    )
    return await store.put_router_model_version(
        RouterModelVersion.model_validate(version_payload)
    )


# --------------------------------------------------------------------------------------
# Fakes. Hand written, with a call counter and a failure switch, and no mocks anywhere.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StubPredictor:
    """A predictor whose five estimates are chosen by the test rather than fitted.

    Real predictions would make the property test a test of the trainer's numbers. What
    AC4-M5-021 is about is what the scorer does *to* a prediction once cross-domain evidence
    is in play, so the prediction itself is an input.
    """

    mean: float
    lower: float
    version_id: str = "rmv_stub"

    def _estimate(self, mean: float, lower: float) -> DistributionEstimate:
        return DistributionEstimate(
            mean=mean,
            lower_bound=lower,
            upper_bound=1.0,
            confidence=0.66,
            method="stub/1",
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
                calibration_version="stub-calibration/1",
            ),
        )


class StubLoader(LearnedPredictorLoader):
    """The real loader with its assembly replaced, so the store lookups stay real.

    A subclass and not a stand-in object: ``score`` reads the version and the training
    snapshot back through ``self.store``, and those reads are part of what the vocabulary
    rung is. Only the expensive part — decoding five bagged heads — is substituted.
    """

    def __init__(
        self, store: MemoryStore, artifacts: ArtifactStore, predictor: StubPredictor
    ) -> None:
        super().__init__(store, artifacts)
        self.predictor = predictor
        self.assemble_calls = 0
        self.failure: Exception | None = None

    def assemble(self, version: RouterModelVersion) -> Any:
        self.assemble_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.predictor


class ExplodingRetriever:
    """An evidence retriever that fails the way an unavailable P7 index would."""

    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, **_: Any) -> Sequence[ExperienceRecord]:
        self.calls += 1
        raise RuntimeError("experience index unavailable")


class FixedRetriever:
    """An evidence retriever that answers with a fixed, ordered list."""

    def __init__(self, records: Sequence[ExperienceRecord]) -> None:
        self.records = tuple(records)
        self.calls = 0

    async def retrieve(self, **_: Any) -> Sequence[ExperienceRecord]:
        self.calls += 1
        return self.records


def _service(execution, **overrides) -> DefaultNodeRoutingService:
    """The fixture's routing service with different stage collaborators attached."""

    return DefaultNodeRoutingService(
        store=execution.service.store,
        snapshots=execution.service.snapshots,
        catalog_factory=execution.service.catalog_factory,
        runtimes=execution.service.runtimes,
        **overrides,
    )


# --------------------------------------------------------------------------------------
# AC4-M5-021
# --------------------------------------------------------------------------------------


def _cross_domain_record(template: ExperienceRecord, *, passed: bool) -> ExperienceRecord:
    """One record whose contract signature is deliberately not the node's.

    Everything else — the workspace, the project, the configuration hash — matches, so the
    only reason it is out of domain is the property §7.10 says makes it out of domain. A
    record that differed in several ways would let a scorer exclude it for the wrong one.
    """

    payload = template.model_dump(mode="python")
    payload.update(
        contract_id=new_id("experience"),
        content_hash="",
        local_verification_status="PASS" if passed else "FAIL",
        final_run_status="PASS" if passed else "FAIL",
        # ADR-048: only a locally verified outcome is evidence a router may learn from, so a
        # failed record is on record but not eligible. Both kinds are retrievable, which is
        # the point — a cross-domain success rate has to be able to be low.
        eligible_for_learning=passed,
        contract_signature={
            **template.contract_signature.model_dump(mode="python"),
            "objective_digest": "f" * 64,
        },
    )
    return ExperienceRecord.model_validate(payload)


@pytest.mark.acceptance("AC4-M5-021")
async def test_cross_domain_evidence_moves_the_mean_within_the_cap_and_never_the_bound(
    tmp_path: Path,
) -> None:
    """AC4-M5-021 over five hundred seeded draws, with an adapter installed to make it hard.

    Each draw scores one candidate twice under identical inputs except for out-of-domain
    evidence, and compares. Three assertions, and each kills a different mutation:

    * the bound is *identical*, which fails the moment cross-domain rows are counted into
      ``n_same_signature`` — the adapter's serving-time influence is ``n / (n + k)``, so a
      mis-scoped count immediately gives the residual authority it has not earned and the
      shifted bound stops matching;
    * the mean moved by at most :data:`CROSS_DOMAIN_CAP`, which fails if the cap is raised,
      because ``n_cross`` runs up to forty against a half-trust count of twenty and the
      uncapped shrinkage weight would be two thirds;
    * the decision is a fallback or a review, which fails if any of the above lets a
      candidate whose bound is below the floor reach ``EXPLOIT``.
    """

    assert CROSS_DOMAIN_CAP == OQ_408_CAP
    execution = await _routable_execution(tmp_path)
    node = execution.frozen.node_contract
    objective = await execution.store.get_objective_contract(
        execution.frozen.objective_ref.objective_contract_id
    )
    assert objective is not None
    context = await _seeded_context(execution)
    candidate = await _seeded_candidate(execution, context)
    signature = node_signature(
        node, objective_digest=execution.frozen.objective_ref.objective_contract_hash
    )
    template = _evidence_template(node, candidate, signature)
    passing = [_cross_domain_record(template, passed=True) for _ in range(CROSS_DOMAIN_POOL)]
    failing = [_cross_domain_record(template, passed=False) for _ in range(CROSS_DOMAIN_POOL)]

    store = MemoryStore()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    # A deliberately opinionated adapter. With `n_project == 0` its influence is exactly
    # zero, so it must change nothing; if the scorer ever hands it a count that includes
    # cross-domain rows, a bias this large moves the bound visibly.
    adapter_artifact = AdapterArtifact(
        bias=2.0, slope=0.0, l2=1.0, n_fit=64, k=8.0, seed=1, prior_version_id="rmv_stub"
    )
    adapter_digest = artifacts.save(adapter_artifact.model_dump_json().encode())
    versions = await _install_property_versions(store, adapter_digest)

    rng = random.Random(SEED)
    hash_key = candidate.configuration.configuration_hash
    for draw in range(DRAWS):
        lower = rng.uniform(0.0, FLOOR - 0.05)
        mean = rng.uniform(lower + 0.25, 0.95)
        n_pass = rng.randint(0, CROSS_DOMAIN_POOL)
        n_fail = rng.randint(0, CROSS_DOMAIN_POOL)
        records = [*passing[:n_pass], *failing[:n_fail]]
        loader = StubLoader(store, artifacts, StubPredictor(mean=mean, lower=lower))
        scorer = ColdStartScorer(loader, artifacts, lambda: datetime(2026, 5, 1, tzinfo=UTC))

        with_cross = await scorer.score(
            context=context,
            candidates=[candidate],
            node=node,
            objective=objective,
            versions=versions,
            evidence_by_hash={hash_key: records},
        )
        without_cross = await scorer.score(
            context=context,
            candidates=[candidate],
            node=node,
            objective=objective,
            versions=versions,
            evidence_by_hash={hash_key: []},
        )

        scored = with_cross.candidates[0]
        clean = without_cross.candidates[0]
        assert with_cross.labels.get(DEGRADED_LABEL) is None, draw
        assert scored.lower_confidence_success == clean.lower_confidence_success, draw
        moved = (
            scored.predicted.node_verified_success.mean
            - clean.predicted.node_verified_success.mean
        )
        assert moved <= OQ_408_CAP + 1e-9, (draw, n_pass, n_fail, moved)
        assert abs(moved) <= OQ_408_CAP + 1e-9, (draw, n_pass, n_fail, moved)

        selection = DeterministicSelector().select(
            [_with_fallback(scored, eligible=draw % 2 == 0)],
            verified_success_floor=FLOOR,
            utility_weights=objective.utility_weights,
            created_at=context.requested_at,
            created_by=node.created_by,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
        )
        assert selection.decision_type in {
            DecisionType.FALLBACK,
            DecisionType.HUMAN_REVIEW_REQUIRED,
        }, draw

    # The adapter really was consulted, so "the bound did not move" is a statement about a
    # live correction rather than about a component that was never reached.
    assert ProjectAdapter.apply(adapter_artifact, 0.0, n_project=8) != 0.0


async def _install_property_versions(
    store: MemoryStore, adapter_digest: str
) -> ActiveVersions:
    """The two rows :meth:`ColdStartScorer._predictor` and ``._adapter`` read back.

    Real rows in a real store rather than a hand-made ``ActiveVersions``: the scorer looks
    the version up, reads its training snapshot for the vocabulary digest, and addresses the
    adapter artefact by the version's ``calibration_artifact_digest``. Short-circuiting any
    of those would leave the property test proving a formula instead of the shipped path.
    """

    principal = PrincipalRef(
        principal_id=new_id("principal"),
        display_name="M5 property",
        status=PrincipalStatus.ACTIVE,
    )
    workspace_id = new_id("workspace_entity")
    snapshot = _property_snapshot(workspace_id)
    await store.put_router_training_snapshot(snapshot)
    minted: list[str] = []
    for digest in ("c" * 64, adapter_digest):
        contract_id = new_id("router_model_version")
        minted.append(contract_id)
        await store.put_router_model_version(
            RouterModelVersion.model_validate(
                {
                    "contract_id": contract_id,
                    "created_at": datetime(2026, 4, 1, tzinfo=UTC),
                    "created_by": principal.model_dump(mode="python"),
                    "workspace_id": workspace_id,
                    "project_id": None,
                    "scope": "TEAM_WORKSPACE",
                    "algorithm_id": "gbdt-bagged-v1",
                    "feature_schema_version": "1.0.0",
                    "training_snapshot_id": snapshot.contract_id,
                    "artifact_digest": "d" * 64,
                    "calibration_artifact_digest": digest,
                    "status": "SHADOW",
                }
            )
        )
    prior, adapter = minted
    return ActiveVersions(
        router_version_id=prior,
        adapter_version_id=adapter,
        router_label=prior,
        adapter_label=adapter,
    )


def _property_snapshot(workspace_id: str) -> RouterTrainingSnapshot:
    """A snapshot that names the scorer's own vocabulary, so the ladder stays out of the way.

    Built from the committed fixture rather than by hand, for the reason every other
    document in this file is: a snapshot assembled from literals would drift from the
    contract the moment a field moved, and the property test would start failing for a
    reason that has nothing to do with cross-domain evidence.
    """

    return sealed(
        RouterTrainingSnapshot,
        workspace_id=workspace_id,
        project_id=new_id("project"),
        labels={VOCABULARY_DIGEST_LABEL: VOCABULARY.digest()},
    )


def _with_fallback(
    candidate: ConfigurationCandidate, *, eligible: bool
) -> ConfigurationCandidate:
    """The same candidate, with its audited-fallback flag set, so both refusals are reached."""

    payload = candidate.model_dump(mode="python")
    payload.update(fallback_eligible=eligible, content_hash="")
    return ConfigurationCandidate.model_validate(payload)


async def _seeded_context(execution) -> RoutingContext:
    """A persisted routing context for the fixture's node, built the way the service builds one."""

    receipt = await _service(execution).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    context = await execution.store.get_routing_request(receipt.routing_request_id)
    assert context is not None
    return context


async def _seeded_candidate(execution, context: RoutingContext) -> ConfigurationCandidate:
    """The candidate the fixture's catalog actually built, read back from the store."""

    candidates = await execution.store.list_configuration_candidates(
        workspace_id=context.workspace_id, project_id=context.project_id
    )
    assert candidates
    return candidates[0]


def _evidence_template(
    node: NodeContract,
    candidate: ConfigurationCandidate,
    signature: ContractSignature,
) -> ExperienceRecord:
    """One in-domain record for this node, from which out-of-domain variants are derived."""

    return sealed(
        ExperienceRecord,
        workspace_id=node.workspace_id,
        project_id=node.project_id or "prj_m5",
        source_node_execution_id=node.execution_instance_id,
        configuration_hash=candidate.configuration.configuration_hash,
        contract_signature=signature.model_dump(mode="python"),
        eligible_for_learning=True,
        local_verification_status="PASS",
        final_run_status="PASS",
    )


# --------------------------------------------------------------------------------------
# §18.5 scenario 1 and the §15.1 ladders
# --------------------------------------------------------------------------------------


async def test_a_fresh_project_routes_on_the_workspace_prior_and_records_no_adapter(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """§18.5 #1: a new project cold starts on the workspace prior and falls back.

    Everything the receipt claims is read back from the store. The three things it must say
    are that the promoted workspace version decided, that no project adapter did, and that
    the outcome was the audited fallback rather than an exploit — a router with no evidence
    about a fresh project has nothing to exploit, and §9.4 makes that a fallback rather than
    a confident guess.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    version = await install_router(execution.store, workspace_id, learned)

    service = _service(
        execution,
        scorer=ColdStartScorer(
            LearnedPredictorLoader(execution.store, learned.artifacts),
            learned.artifacts,
            lambda: datetime(2026, 5, 1, tzinfo=UTC),
        ),
        default_mode=RoutingMode.AUTO,
    )
    receipt = await service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.decision_type is DecisionType.FALLBACK
    assert stored.workspace_router_version == version.contract_id
    assert stored.project_adapter_version is None
    assert stored.labels[DEGRADED_LABEL] == ADAPTER_UNAVAILABLE
    assert stored.experience_refs == []
    assert stored.uncertainty.calibration_version != "cold-start-prior/1"
    assert service.default_mode is RoutingMode.AUTO


async def test_a_prior_that_will_not_load_falls_back_to_the_deterministic_router(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """§15.1 "workspace model unavailable: use audited deterministic baseline".

    The receipt names ``deterministic-router/1`` and not the version that was in force,
    because ``workspace_router_version`` is an attribution: naming a model that refused to
    load would credit a deterministic fallback's outcomes to it in every later evaluation.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    await install_router(execution.store, workspace_id, learned)

    loader = StubLoader(
        execution.store, learned.artifacts, StubPredictor(mean=0.9, lower=0.8)
    )
    loader.failure = ArtifactDigestMismatchError("artefact bytes moved under the digest")
    receipt = await _service(
        execution,
        scorer=ColdStartScorer(
            loader, learned.artifacts, lambda: datetime(2026, 5, 1, tzinfo=UTC)
        ),
    ).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert loader.assemble_calls == 1
    assert stored.workspace_router_version == WORKSPACE_ROUTER_VERSION
    assert stored.project_adapter_version is None
    assert stored.labels[DEGRADED_LABEL] == WORKSPACE_MODEL_UNAVAILABLE
    assert stored.uncertainty.calibration_version == "cold-start-prior/1"
    assert stored.decision_type is DecisionType.FALLBACK


async def test_an_unevaluated_version_is_refused_by_the_real_loader_and_degrades(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """AC4-M4-016's refusal, seen from the router: it degrades rather than raising.

    The version below is genuinely present and genuinely unroutable — RETIRED — and the
    loader is the real one, so what is exercised is M4's gate and not a stand-in for it. A
    router that let the refusal escape would fail a run over a model it was never obliged to
    consult.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    retired = await install_router(
        execution.store, workspace_id, learned, status=RouterStatus.RETIRED
    )

    loader = LearnedPredictorLoader(execution.store, learned.artifacts)
    with pytest.raises(RouterNotEvaluatedError):
        await loader.load(retired.contract_id)

    receipt = await _service(
        execution,
        active_versions=_FixedVersions(
            ActiveVersions(
                router_version_id=retired.contract_id,
                adapter_version_id=None,
                router_label=retired.contract_id,
                adapter_label=None,
            )
        ),
        scorer=ColdStartScorer(
            loader, learned.artifacts, lambda: datetime(2026, 5, 1, tzinfo=UTC)
        ),
    ).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.labels[DEGRADED_LABEL] == WORKSPACE_MODEL_UNAVAILABLE
    assert stored.workspace_router_version == WORKSPACE_ROUTER_VERSION


async def test_a_snapshot_under_another_vocabulary_is_refused_rather_than_absorbed(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """§7.12: the same feature row means something else under another token table.

    The version is loadable and the artefacts are intact; only the vocabulary digest the
    snapshot recorded has moved. A scorer that predicted anyway would be reading columns
    whose categorical indices point at different tokens, which is silent at every other
    layer and would look like a merely worse model.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    await install_router(
        execution.store, workspace_id, learned, vocabulary_digest="e" * 64
    )

    loader = StubLoader(
        execution.store, learned.artifacts, StubPredictor(mean=0.95, lower=0.9)
    )
    receipt = await _service(
        execution,
        scorer=ColdStartScorer(
            loader, learned.artifacts, lambda: datetime(2026, 5, 1, tzinfo=UTC)
        ),
    ).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert stored.labels[DEGRADED_LABEL] == VOCABULARY_MISMATCH
    assert stored.workspace_router_version == WORKSPACE_ROUTER_VERSION
    # A confident stub was available and was not used: the mismatch is what decided.
    assert stored.decision_type is DecisionType.FALLBACK


async def test_a_retriever_that_raises_leaves_the_receipt_without_experience_refs(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """§15.1 "evidence retrieval unavailable: do not fabricate history".

    Two halves. The receipt cites no experience, because there is none it may cite; and it
    says ``EVIDENCE_UNAVAILABLE``, because "we could not read the history" and "there was no
    history" are different statements and only the second one is what an empty
    ``experience_refs`` would otherwise mean.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    version = await install_router(execution.store, workspace_id, learned)
    retriever = ExplodingRetriever()

    receipt = await _service(
        execution,
        evidence=retriever,
        scorer=ColdStartScorer(
            LearnedPredictorLoader(execution.store, learned.artifacts),
            learned.artifacts,
            lambda: datetime(2026, 5, 1, tzinfo=UTC),
        ),
    ).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert retriever.calls == 1
    assert stored.experience_refs == []
    assert stored.labels[DEGRADED_LABEL] == EVIDENCE_UNAVAILABLE
    # The prior still decided, so the attribution stays with it: a failed retrieval is not
    # a failed model.
    assert stored.workspace_router_version == version.contract_id
    context = await execution.store.get_routing_request(stored.routing_request_id)
    assert context is not None
    assert context.project_features.observed_task_count == 0


async def test_retrieved_evidence_reaches_the_receipt_and_the_project_feature_count(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The positive case the degradation tests are the absence of.

    Without this the ladder tests would pass against a router that never retrieved anything
    at all. What is asserted is that the records a retriever returned are cited by the
    receipt and counted in the routing context's ``observed_task_count``.
    """

    learned = await learned_once(tmp_path_factory)
    execution = await _routable_execution(tmp_path)
    workspace_id = execution.frozen.node_contract.workspace_id
    await install_router(execution.store, workspace_id, learned)
    node = execution.frozen.node_contract
    signature = node_signature(
        node, objective_digest=execution.frozen.objective_ref.objective_contract_hash
    )
    context = await _seeded_context(execution)
    candidate = await _seeded_candidate(execution, context)
    template = _evidence_template(node, candidate, signature)
    records = [_cross_domain_record(template, passed=True) for _ in range(3)]
    retriever = FixedRetriever(records)

    receipt = await _service(
        execution,
        evidence=retriever,
        scorer=ColdStartScorer(
            LearnedPredictorLoader(execution.store, learned.artifacts),
            learned.artifacts,
            lambda: datetime(2026, 5, 1, tzinfo=UTC),
        ),
    ).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.AUTO,
        run=execution.run,
    )

    stored = await execution.store.get_routing_receipt(receipt.contract_id)
    assert stored is not None
    assert retriever.calls == 1
    assert sorted(stored.experience_refs) == sorted(
        record.contract_id for record in records
    )
    assert stored.labels[DEGRADED_LABEL] == ADAPTER_UNAVAILABLE
    learned_context = await execution.store.get_routing_request(stored.routing_request_id)
    assert learned_context is not None
    assert learned_context.project_features.observed_task_count == len(records)


class _FixedVersions:
    """An :class:`ActiveVersionResolver` that answers with one prepared record."""

    def __init__(self, versions: ActiveVersions) -> None:
        self.versions = versions
        self.calls = 0

    async def resolve(self, *, workspace_id: str, project_id: str) -> ActiveVersions:
        self.calls += 1
        return self.versions
