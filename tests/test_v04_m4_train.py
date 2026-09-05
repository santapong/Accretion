"""Training a router candidate, and the one thing that may never happen without it.

AC4-M4-016 — "offline ranking precedes any shadow or live learned policy" — is a *negative*
claim, so the first test here is the one that tries to do the forbidden thing: load a
learned predictor for a version that was never evaluated. Everything else exists to make
that refusal meaningful rather than vacuous. A trainer that produced no candidate at all
would pass a negative test trivially, so the same corpus proves that the trained candidate
*does* load, that its holdout was scored on projects the fit never saw, and that the
snapshot naming that evidence was written before the version that cites it.

**Why the corpus is built out of the committed fixtures.** A hand-rolled
:class:`~accretion.contracts.routing.RoutingContext` would drift from the sealed one the
moment a field moved, and the join this module exercises — experience record → node contract
→ routing context → configuration candidate → objective contract — is exactly the place that
drift would hide. Each record is a re-tenanted, re-sealed copy of the golden document, so a
contract change breaks this file loudly.

**Why one training run is shared.** Fitting five bagged heads is the expensive thing here,
and the acceptance-marked tests are budgeted at two seconds each, so the corpus is built and
trained **once** per module and cached. Tests that must vary an input (the seed, the amount
of evidence, the bytes on disk) build their own, and no test mutates a document another one
reads.

There is no ``conftest.py``. Every builder below is module-local, the store is a fresh
:class:`~accretion.persistence.store.MemoryStore` subclass that records the order of the two
writes the acceptance criterion is about, and every assertion is made against what the store
gives back rather than against the object handed to it.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import accretion
from accretion.contracts import (
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    TaskType,
)
from accretion.contracts.canonical import CanonicalContract, canonical_json
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ExecutionConfiguration,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RoutingContext,
)
from accretion.experience.models import (
    Experience,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.calibration import CalibrationReport
from accretion.routing.features import Vocabulary
from accretion.routing.ranker import ArtifactDigestMismatchError
from accretion.routing.split import SplitName
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    ALGORITHM_ID,
    CALIBRATION_REPORT_LABEL,
    SPLIT_ASSIGNMENT_LABEL,
    HoldoutEvaluation,
    LearnedPredictorLoader,
    RouterNotEvaluatedError,
    RouterTrainingService,
    SnapshotConflictError,
    TrainedCandidate,
    TrainingConfig,
    TrainingDataError,
)
from accretion.routing.training_snapshot import SnapshotRules, TrainingTable, materialize

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"

WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC))
FIRST_RECORD_AT = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)
SEED = 11

STRONG_MODEL = "fixture-strong-model"
WEAK_MODEL = "fixture-weak-model"
RUNTIME_MODEL = "fake"

VOCABULARY = Vocabulary.frozen_over(
    model_ids=[RUNTIME_MODEL, STRONG_MODEL, WEAK_MODEL],
    adapter_versions=["accretion-claude-v1"],
)
RULES = SnapshotRules.over(
    provider_version_boundaries={"FAKE": "<=fake-1"},
    vocabulary=VOCABULARY,
)

TEST_CONFIG = TrainingConfig(
    n_trees=8,
    max_depth=2,
    bags=2,
    # Two calibration projects is what the five-way split gives a ten-project workspace, and
    # `conformal_quantile` returns a vacuous 1.0 whenever the group count is below
    # ``1/alpha - 1``. At the default alpha of 0.05 that needs nineteen projects, which no
    # two-second test is going to build; at 0.34 two groups are enough for the quantile to
    # be a real number, so the lower bounds these tests read are real lower bounds.
    alpha=0.34,
    bootstraps=20,
)
"""Small enough to fit in the budget, large enough that every statistic is computed."""


def frozen_clock() -> datetime:
    """The one instant every document in this file is stamped with.

    A snapshot's and a version's ``created_at`` are both inside their ``content_hash``, so
    two runs can only be byte-identical if the clock is.
    """

    return datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def digest_of(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def rescope(value: Any, *, workspace_id: str, project_id: str) -> Any:
    """Re-tenant a fixture document, nested records included.

    Several v0.4 contracts refuse a document whose *embedded* records were computed in
    another workspace or project — ``RoutingContext`` checks it on its feature vectors —
    so rewriting only the top level would produce a document that cannot be built. Every
    nested ``content_hash`` is dropped for the same reason the top-level one is: each
    sealed record reseals itself over its new body, while a reference's ``content_hash`` is
    the required digest of something else and must survive.
    """

    if isinstance(value, dict):
        sealed = "contract_type" in value
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if key == "content_hash" and sealed:
                continue
            if key == "workspace_id":
                rewritten[key] = workspace_id
            elif key == "project_id" and isinstance(item, str):
                rewritten[key] = project_id
            else:
                rewritten[key] = rescope(item, workspace_id=workspace_id, project_id=project_id)
        return rewritten
    if isinstance(value, list):
        return [rescope(item, workspace_id=workspace_id, project_id=project_id) for item in value]
    return value


def build[C: CanonicalContract](
    model: type[C], *, workspace_id: str, project_id: str, **overrides: Any
) -> C:
    """One committed ``minimal.json``, re-tenanted to this run's ids and re-sealed."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document = rescope(document, workspace_id=workspace_id, project_id=project_id)
    document.update(overrides)
    for name in ("content_hash", *model.DERIVED_HASH_FIELDS):
        document.pop(name, None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


class RecordingStore(MemoryStore):
    """A memory store that remembers the order of the two writes AC4-M4-016 is about.

    A subclass and not a mock: every other method behaves exactly as the real store does,
    including the append-only refusals, so a test that reads a document back is reading it
    from the same code path production uses.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def put_router_training_snapshot(self, record: Any) -> Any:
        self.calls.append("put_router_training_snapshot")
        return await super().put_router_training_snapshot(record)

    async def put_router_model_version(self, record: Any) -> Any:
        self.calls.append("put_router_model_version")
        return await super().put_router_model_version(record)


@dataclass(frozen=True, slots=True)
class Corpus:
    """One workspace's worth of evidence and the decisions it was evidence about."""

    store: RecordingStore
    workspace_id: str
    principal: PrincipalRef
    project_ids: tuple[str, ...]
    record_ids: tuple[str, ...]


async def seed_experience(store: MemoryStore, experience_id: str, project_id: str) -> None:
    """The v0.2 P7 experience an ``ExperienceRecord`` of that id projects (ADR-054 b).

    ``experience_records.id`` *is* the key into ``experiences``, and ``retracted`` and
    ``revision`` live only here, so a projection without one of these is a record of nothing
    and the snapshot builder drops it.
    """

    experience = Experience(
        experience_id=experience_id,
        project_id=project_id,
        repository_identity=digest_of(project_id),
        task_id=new_id("task"),
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=new_id("run"),
        source_commit="b" * 40,
        architecture_version="2.0",
        manifest_digest=digest_of(f"manifest-{experience_id}"),
        policy_digest=digest_of(f"policy-{experience_id}"),
        verifier_digest=digest_of(f"verifier-{experience_id}"),
        prompt_digest=digest_of(f"prompt-{experience_id}"),
        context_digest=digest_of(f"context-{experience_id}"),
        tool_profile_digest=digest_of(f"tools-{experience_id}"),
        provider=Provider.FAKE,
        runtime_model=RUNTIME_MODEL,
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest_of(f"experience-{experience_id}"),
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest_of(f"segment-{experience_id}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience_id,
        input_digest=digest_of(f"embedding-{experience_id}"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)


def configuration_for(
    *, workspace_id: str, project_id: str, strong: bool
) -> ExecutionConfiguration:
    """One of two candidate configurations, differing where the learner can see it.

    The model id is the only field that moves, and it is the field the outcome depends on,
    so a ranker that learned nothing would be visibly indistinguishable from one that did.
    """

    document: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "execution_configuration" / "minimal.json").read_text(encoding="utf-8")
    )
    document = rescope(document, workspace_id=workspace_id, project_id=project_id)
    document["model"] = {
        **document["model"],
        "model_id": STRONG_MODEL if strong else WEAK_MODEL,
    }
    document.pop("content_hash", None)
    document.pop("configuration_hash", None)
    document["contract_id"] = new_id("execution_configuration")
    return ExecutionConfiguration.model_validate(document)


async def setup_corpus(*, projects: int = 10, per_project: int = 4) -> Corpus:
    """A workspace where every eligible record still has the decision it was an outcome of.

    ``projects`` and ``per_project`` are arguments because the refusal tests need a corpus
    that is deliberately too small, and building one by deleting rows from a good corpus
    would prove less: the store would still hold the orphaned decisions.
    """

    store = RecordingStore()
    workspace_id = f"wks_{uuid4().hex[:12]}"
    principal = PrincipalRef(
        principal_id=f"usr_{uuid4().hex[:12]}",
        display_name="v0.4 M4 trainer",
        status=PrincipalStatus.ACTIVE,
    )
    project_ids: list[str] = []
    record_ids: list[str] = []
    minted = 0

    for project_index in range(projects):
        project_id = new_id("project")
        project_ids.append(project_id)
        await store.create_project(
            Project(
                project_id=project_id,
                name=f"v0.4 M4 training project {project_index}",
                repository_path=Path("/tmp/accretion-v04-m4-train"),
            )
        )
        objective = build(
            ObjectiveContract, workspace_id=workspace_id, project_id=project_id
        )
        await store.put_objective_contract(objective)

        for slot in range(per_project):
            strong = slot % 2 == 0
            minted += 1
            execution_instance_id = f"exe_{uuid4().hex[:16]}"
            node_document = json.loads(
                (FIXTURE_ROOT / "node_contract" / "minimal.json").read_text(encoding="utf-8")
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
                node_id=f"node-{project_index}-{slot}",
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
                workspace_id=workspace_id, project_id=project_id, strong=strong
            )
            candidate = build(
                ConfigurationCandidate,
                workspace_id=workspace_id,
                project_id=project_id,
                routing_request_id=context.contract_id,
                configuration=configuration.model_dump(mode="json"),
            )
            await store.put_configuration_candidate(candidate)

            experience_id = new_id("experience")
            await seed_experience(store, experience_id, project_id)
            record = build(
                ExperienceRecord,
                workspace_id=workspace_id,
                project_id=project_id,
                contract_id=experience_id,
                created_at=(FIRST_RECORD_AT + timedelta(hours=minted)).isoformat(),
                source_node_execution_id=execution_instance_id,
                configuration_hash=candidate.configuration.configuration_hash,
                eligible_for_learning=True,
                local_verification_status="PASS",
                # The label the run concluded with, which is the only one that varies inside
                # a snapshot: `eligible_for_learning` already forces every *local* status to
                # PASS, so a node that passed inside a run that later failed is where all of
                # this milestone's label variation comes from.
                final_run_status="PASS" if strong else "FAIL",
                outcomes={
                    "quality": 0.9 if strong else 0.3,
                    "cost": "2.50" if strong else "4.50",
                    "latency_ms": 20_000 if strong else 60_000,
                },
            )
            await store.put_experience_record(record)
            record_ids.append(experience_id)

    return Corpus(
        store=store,
        workspace_id=workspace_id,
        principal=principal,
        project_ids=tuple(project_ids),
        record_ids=tuple(record_ids),
    )


async def train(
    corpus: Corpus,
    artifacts: ArtifactStore,
    *,
    seed: int = SEED,
    config: TrainingConfig = TEST_CONFIG,
    **overrides: Any,
) -> TrainedCandidate:
    return await RouterTrainingService(
        corpus.store, artifacts, clock=frozen_clock, config=config
    ).train_candidate(
        workspace_id=corpus.workspace_id,
        window=WINDOW,
        seed=seed,
        created_by=corpus.principal,
        rules=RULES,
        **overrides,
    )


_TRAINED: tuple[Corpus, ArtifactStore, TrainedCandidate, tuple[str, ...]] | None = None


async def trained_once(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Corpus, ArtifactStore, TrainedCandidate, tuple[str, ...]]:
    """Build and train the shared corpus once, and hand back the call order it produced.

    The recorded call list is snapshotted *at the moment training finished* rather than read
    later, so a test that writes its own row into the same store cannot move the order
    another test asserts on.
    """

    global _TRAINED
    if _TRAINED is None:
        corpus = await setup_corpus()
        artifacts = ArtifactStore(tmp_path_factory.mktemp("router-artifacts"))
        candidate = await train(corpus, artifacts)
        _TRAINED = (corpus, artifacts, candidate, tuple(corpus.store.calls))
    return _TRAINED


@pytest.mark.acceptance("AC4-M4-016")
async def test_a_learned_version_without_a_holdout_evaluation_cannot_be_loaded_for_routing(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The gate is the label, not the status: a CANDIDATE alone is not evidence of anything.

    The unevaluated version below is deliberately *complete* in every other respect — same
    artefact digest, same calibration digest, same snapshot, same CANDIDATE status, and it
    even carries the calibration report label — so the only thing standing between it and a
    live predictor is ``holdout_eval_digest``.
    """

    corpus, artifacts, trained, _ = await trained_once(tmp_path_factory)
    loader = LearnedPredictorLoader(corpus.store, artifacts)

    unevaluated = RouterModelVersion.model_validate(
        {
            "contract_id": new_id("router_model_version"),
            "created_at": frozen_clock(),
            "created_by": corpus.principal,
            "workspace_id": corpus.workspace_id,
            "scope": RouterScope.TEAM_WORKSPACE,
            "algorithm_id": ALGORITHM_ID,
            "feature_schema_version": trained.version.feature_schema_version,
            "training_snapshot_id": trained.snapshot.contract_id,
            "artifact_digest": trained.version.artifact_digest,
            "calibration_artifact_digest": trained.version.calibration_artifact_digest,
            "status": RouterStatus.CANDIDATE,
            "labels": {
                CALIBRATION_REPORT_LABEL: trained.version.labels[CALIBRATION_REPORT_LABEL]
            },
        }
    )
    await corpus.store.put_router_model_version(unevaluated)

    with pytest.raises(RouterNotEvaluatedError, match=ACCEPTANCE_LABEL):
        await loader.load(unevaluated.contract_id)

    # And the same refusal for a version that *was* evaluated but has been retired: a
    # rollback target's bytes survive precisely so they are not routed on.
    retired = RouterModelVersion.model_validate(
        {
            **unevaluated.model_dump(mode="json"),
            "contract_id": new_id("router_model_version"),
            "content_hash": "",
            "status": RouterStatus.RETIRED,
            "labels": dict(trained.version.labels),
        }
    )
    await corpus.store.put_router_model_version(retired)
    with pytest.raises(RouterNotEvaluatedError, match="RETIRED"):
        await loader.load(retired.contract_id)

    # The trained candidate, read back from the store rather than reused, does load — so
    # the refusal above is about the missing evaluation and not about the loader being
    # unable to assemble anything at all.
    stored = await corpus.store.get_router_model_version(trained.version.contract_id)
    assert stored is not None
    predictor = await loader.load(stored.contract_id)
    assert predictor.artifact.feature_schema_version == stored.feature_schema_version
    predicted, uncertainty = predictor.predict([0.0] * predictor.artifact.n_features)
    assert 0.0 <= predicted.run_verified_success.lower_bound <= 1.0
    assert uncertainty.calibration_version == predictor.calibrator.version


@pytest.mark.acceptance("AC4-M4-016")
async def test_train_candidate_records_snapshot_holdout_and_calibration_before_the_version_exists(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The evidence is on record first, and the version cites it — never the other way round.

    A version written before its snapshot would name, for the width of that window, evidence
    nobody could read; and a version whose digests were labels rather than stored documents
    would make AC4-M4-016 a string comparison. Both are checked: the order, and that each
    digest resolves to a document that parses.
    """

    corpus, artifacts, trained, calls = await trained_once(tmp_path_factory)

    assert calls.index("put_router_training_snapshot") < calls.index("put_router_model_version")

    version = await corpus.store.get_router_model_version(trained.version.contract_id)
    snapshot = await corpus.store.get_router_training_snapshot(trained.snapshot.contract_id)
    assert version is not None and snapshot is not None
    assert version.status is RouterStatus.CANDIDATE
    assert version.training_snapshot_id == snapshot.contract_id
    assert version.labels[ACCEPTANCE_LABEL] == trained.holdout.digest()
    assert version.labels[CALIBRATION_REPORT_LABEL]
    assert version.labels[SPLIT_ASSIGNMENT_LABEL]

    holdout = HoldoutEvaluation.model_validate(
        json.loads(artifacts.load(version.labels[ACCEPTANCE_LABEL]))
    )
    report = CalibrationReport.model_validate(
        json.loads(artifacts.load(version.labels[CALIBRATION_REPORT_LABEL]))
    )
    assert holdout == trained.holdout
    assert report == trained.calibration
    assert holdout.training_snapshot_id == snapshot.contract_id

    # The artefact and the calibration are two digests and not one, because §7.12 and
    # OQ-405 let a recalibration produce a new version without retraining a tree.
    assert version.artifact_digest != version.calibration_artifact_digest
    assert artifacts.load(version.artifact_digest)
    assert artifacts.load(version.calibration_artifact_digest)

    # The evaluation says what it measured, and it measured something: an evaluation with no
    # rows would satisfy every assertion above and prove nothing.
    assert holdout.n_rows >= 4
    assert 0.0 <= holdout.ece_10bin <= 1.0
    assert 0.0 <= holdout.brier <= 1.0
    assert holdout.verified_success_lcb <= holdout.observed_verified_success_rate
    assert holdout.per_cohort
    assert all(item.cohort_id.split(":")[0] in {"PROVIDER", "RISK"} for item in holdout.per_cohort)
    assert holdout.ranking_concordance is not None
    assert holdout.baseline_ranking_concordance is not None
    assert holdout.ranking_gain is not None
    assert report.conformal_quantile < 1.0


def _dotted_callee(node: ast.expr) -> str | None:
    """``a.b.C.load`` as a string, for a callee expression that is a plain dotted name."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_callee(node.value)
        return None if prefix is None else f"{prefix}.{node.attr}"
    return None


def _predictor_assembly_sites() -> set[str]:
    """Every module under ``src/accretion`` that names ``LearnedOutcomePredictor`` in a call.

    Parsed rather than imported, so a module that is only reachable from a route or a
    lifespan — the two places a future dispatcher would live — is still seen.
    """

    root = Path(accretion.__file__).parent
    sites: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _assembles_learned_predictor(tree):
            sites.add(path.relative_to(root).as_posix())
    return sites


def _assembles_learned_predictor(tree: ast.AST) -> bool:
    """Whether a parsed module calls ``LearnedOutcomePredictor`` under any local name.

    The binding is resolved, not the spelling: ``from ... import LearnedOutcomePredictor as P``
    followed by ``P(...)`` is the same door as the unaliased call, and a scan that matched the
    literal name would wave it through.
    """

    bound = {"LearnedOutcomePredictor"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "LearnedOutcomePredictor":
                    bound.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_callee(node.func)
        if dotted is not None and bound & set(dotted.split(".")):
            return True
    return False


def test_the_assembly_scan_resolves_import_aliases() -> None:
    """The scan itself is under test, so it cannot silently stop resolving aliases."""

    aliased = ast.parse(
        "from accretion.routing.ranker import LearnedOutcomePredictor as P\n"
        "def door():\n    return P(artifact=None)\n"
    )
    plain = ast.parse("import accretion.routing.ranker as r\nx = r.LearnedOutcomePredictor(1)\n")
    innocent = ast.parse(
        "from accretion.routing.ranker import RankerArtifact\ny = RankerArtifact\n"
    )
    assert _assembles_learned_predictor(aliased)
    assert _assembles_learned_predictor(plain)
    assert not _assembles_learned_predictor(innocent)


@pytest.mark.acceptance("AC4-M4-016")
def test_no_module_outside_the_loader_assembles_a_learned_predictor() -> None:
    """The negative half of AC4-M4-016, enforced structurally rather than by convention.

    Every other test in this file proves that *the loader* refuses an unevaluated version.
    None of them can prove that the loader is the only door, and that is the whole claim:
    :meth:`~accretion.routing.ranker.LearnedOutcomePredictor.load` will hand back a fully
    usable predictor from bytes on disk after checking a digest and nothing else — no
    :class:`~accretion.contracts.routing.RouterModelVersion`, no ``holdout_eval_digest``, no
    evaluation. A future M2.2 dispatcher or M6 shadow evaluator that called that classmethod
    would route on an unevaluated model while every behavioural test here stayed green.

    So the invariant is asserted over the source tree: a learned predictor may be assembled
    in exactly two modules — ``ranker.py``, which defines it, and ``train.py``, which is
    where :class:`LearnedPredictorLoader` lives. Anywhere else is a second door, and the
    failure message names it. A module that legitimately needs a predictor calls the loader;
    a module that legitimately needs to *build* one belongs in this list, and adding it here
    is the deliberate act this test exists to force.
    """

    allowed = {"routing/train.py", "routing/ranker.py"}
    sites = _predictor_assembly_sites()

    # Not vacuous: the trainer really does assemble one, so an empty result would mean the
    # scan stopped working rather than that the invariant holds.
    assert "routing/train.py" in sites
    assert sites <= allowed, (
        "a learned predictor is assembled outside LearnedPredictorLoader: "
        f"{sorted(sites - allowed)}"
    )


@pytest.mark.acceptance("AC4-M4-016")
async def test_the_holdout_projects_are_disjoint_from_training_and_calibration_projects(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Every project the candidate was scored on is one it was never fitted on.

    Checked against the *sealed* snapshot as well as the working assignment, because the
    snapshot is what a later promotion report cites and the assignment is what the trainer
    actually partitioned rows by. If those two ever disagreed, the disjointness proved here
    would not be the disjointness §10.1 seals.
    """

    corpus, _, trained, _ = await trained_once(tmp_path_factory)
    snapshot = await corpus.store.get_router_training_snapshot(trained.snapshot.contract_id)
    assert snapshot is not None

    assignment = trained.assignment
    training = set(assignment.project_ids_for(SplitName.TRAIN))
    calibration = set(assignment.project_ids_for(SplitName.CALIBRATION))
    holdout = set(assignment.project_ids_for(SplitName.TEST)) | set(
        assignment.project_ids_for(SplitName.DRIFT)
    )

    assert training and calibration and holdout
    assert not training & calibration
    assert not holdout & training
    assert not holdout & calibration

    assert set(snapshot.split.training_project_ids) == training
    assert set(snapshot.split.holdout_project_ids) == holdout
    assert calibration <= set(snapshot.split.validation_project_ids)

    # And the evaluation was actually computed over holdout projects, not merely declared to
    # have been: a "holdout" evaluation whose rows came from the training projects is the
    # exact failure this test exists to catch.
    assert set(trained.holdout.project_ids) <= holdout
    assert not set(trained.holdout.project_ids) & training


async def store_holding(corpus: Corpus, records: Sequence[ExperienceRecord]) -> RecordingStore:
    """The corpus's decisions, and only the outcomes named — a second store, not a stub.

    Every routing decision is copied verbatim so the join has the same node contracts,
    contexts, candidates and objectives to work with; only the set of
    :class:`~accretion.contracts.routing.ExperienceRecord` rows changes. That is what makes
    the comparison below a statement about the *evidence window* rather than about anything
    else the featurizer reads.

    The experiences are re-seeded rather than copied because ``experience_records.id`` is a
    foreign key into ``experiences`` and the store enforces it; nothing in the join reads
    the experience itself.
    """

    store = RecordingStore()
    for project in await corpus.store.list_projects():
        await store.create_project(project)
    for objective in await corpus.store.list_objective_contracts(
        workspace_id=corpus.workspace_id
    ):
        await store.put_objective_contract(objective)
    for node in await corpus.store.list_node_contracts(workspace_id=corpus.workspace_id):
        await store.put_node_contract(node)
    for context in await corpus.store.list_routing_requests(workspace_id=corpus.workspace_id):
        await store.put_routing_request(context)
    for candidate in await corpus.store.list_configuration_candidates(
        workspace_id=corpus.workspace_id
    ):
        await store.put_configuration_candidate(candidate)
    for record in records:
        await seed_experience(store, record.contract_id, record.project_id or "")
        await store.put_experience_record(record)
    return store


def restated(record: ExperienceRecord, **overrides: Any) -> ExperienceRecord:
    """The same record with different measurements, re-sealed over its new body.

    ``contract_signature``, ``configuration_hash`` and ``created_at`` are left alone: those
    three are what selects a record into another record's evidence, and moving them would
    change the window instead of what the window summarises.
    """

    document = record.model_dump(mode="json")
    document.update(overrides)
    for name in ("content_hash", *ExperienceRecord.DERIVED_HASH_FIELDS):
        document.pop(name, None)
    return ExperienceRecord.model_validate(document)


async def features_for(
    store: MemoryStore, corpus: Corpus, table: TrainingTable, experience_id: str, root: Path
) -> list[float | None]:
    """Featurize one snapshot row against whatever evidence ``store`` holds."""

    service = RouterTrainingService(
        store, ArtifactStore(root), clock=frozen_clock, config=TEST_CONFIG
    )
    rows = [
        row
        for row in await service._join(corpus.workspace_id, table, RULES)
        if row.experience_id == experience_id
    ]
    assert len(rows) == 1, f"{experience_id} joined {len(rows)} times"
    return rows[0].values


async def test_a_rows_features_summarise_only_its_own_earlier_same_project_evidence(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The leakage control, pinned by construction rather than by declaration.

    ``test_the_holdout_projects_are_disjoint_from_training_and_calibration_projects`` proves
    the *declared* partition of project ids. It cannot prove that the feature vectors respect
    it, and a featurizer that summarised the whole snapshot would satisfy it while putting a
    holdout project's outcomes inside a training row's features — at which point the holdout
    measures memorisation through the back door and "offline ranking" stops being a
    measurement at all.

    So the same holdout row is featurized twice: once against the full corpus, and once
    against a store that *physically cannot* leak, because it holds only that row's own
    project's records from strictly before it. Equality of the two vectors says the window
    the trainer applies is the window the store enforces. Widening
    ``_evidence_for``'s window, or handing ``_join`` the whole record set instead of the
    row's project, moves the first vector and not the second.
    """

    corpus, _, trained, _ = await trained_once(tmp_path_factory)
    snapshot = await corpus.store.get_router_training_snapshot(trained.snapshot.contract_id)
    assert snapshot is not None
    table = await materialize(snapshot, corpus.store, VOCABULARY)

    records = {
        record.contract_id: record
        for record in await corpus.store.list_experience_records(
            workspace_id=corpus.workspace_id
        )
    }
    holdout_projects = set(trained.assignment.project_ids_for(SplitName.TEST)) | set(
        trained.assignment.project_ids_for(SplitName.DRIFT)
    )

    service = RouterTrainingService(
        corpus.store,
        ArtifactStore(tmp_path / "unused-artifacts"),
        clock=frozen_clock,
        config=TEST_CONFIG,
    )
    joined = await service._join(corpus.workspace_id, table, RULES)
    grouped: dict[str, list[Any]] = {}
    for row in joined:
        if row.project_id in holdout_projects:
            grouped.setdefault(row.project_id, []).append(row)

    # A row with evidence before it and outcomes after it, so that both halves of the window
    # — the earlier bound and the later one — have something to exclude.
    chosen = None
    for project_id in sorted(grouped):
        siblings = sorted(
            grouped[project_id], key=lambda row: records[row.experience_id].created_at
        )
        if len(siblings) >= 3:
            chosen = siblings[1]
            break
    assert chosen is not None, "no holdout project has a row with both an earlier and a later"

    target = records[chosen.experience_id]
    same_project = [
        record for record in records.values() if record.project_id == target.project_id
    ]
    earlier = [record for record in same_project if record.created_at < target.created_at]
    later = [record for record in same_project if record.created_at > target.created_at]
    other_projects = [
        record for record in records.values() if record.project_id != target.project_id
    ]
    assert earlier and later and other_projects

    full = await features_for(
        corpus.store, corpus, table, target.contract_id, tmp_path / "a"
    )
    isolated = await features_for(
        await store_holding(corpus, [*earlier, target]),
        corpus,
        table,
        target.contract_id,
        tmp_path / "b",
    )
    assert full == isolated, (
        "the row's features do not match the ones computable from its own project's earlier "
        "records alone, so something later or from another project reached them"
    )

    # The row's own outcome is not in its own features: restate what this record measured,
    # keeping its identity, its signature and its instant, and the vector does not move.
    restated_target = restated(
        target,
        outcomes={"quality": 0.05, "cost": "97.50", "latency_ms": 900_000},
        final_run_status="FAIL" if target.final_run_status.value == "PASS" else "PASS",
    )
    assert restated_target.contract_signature == target.contract_signature
    assert restated_target.configuration_hash == target.configuration_hash
    assert restated_target.outcomes != target.outcomes
    self_excluded = await features_for(
        await store_holding(corpus, [*earlier, restated_target]),
        corpus,
        table,
        target.contract_id,
        tmp_path / "c",
    )
    assert self_excluded == full, (
        "changing what this record measured changed its own feature vector, so the row can "
        "see its own outcome"
    )

    # Not vacuous: the evidence window is read by the featurizer, so removing the earlier
    # records the window admits does move the vector.
    blind = await features_for(
        await store_holding(corpus, [target]), corpus, table, target.contract_id, tmp_path / "d"
    )
    assert blind != full


async def test_training_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    """Same store, same seed, same clock — one candidate, byte for byte.

    Two runs over one store, because that is the claim: reproducibility is a property of the
    inputs, and a trainer that drew its bootstrap from an unordered iteration or minted a
    random version id would produce a second, different candidate here. The seed is then
    changed to show the equality is not vacuous.
    """

    corpus = await setup_corpus()
    artifacts = ArtifactStore(tmp_path / "artifacts")

    first = await train(corpus, artifacts)
    second = await train(corpus, artifacts)

    assert first.version.artifact_digest == second.version.artifact_digest
    assert first.version.contract_id == second.version.contract_id
    assert first.version.content_hash == second.version.content_hash
    assert first.snapshot.content_hash == second.snapshot.content_hash
    assert first.holdout.digest() == second.holdout.digest()

    # The append-only store recognised the second run as the same document rather than as a
    # second version claiming the same id.
    versions = await corpus.store.list_router_model_versions(workspace_id=corpus.workspace_id)
    assert [version.contract_id for version in versions] == [first.version.contract_id]

    # Not vacuous: retune the learner over the same evidence and both the artefact and the
    # version id move, because the version's derived id covers what produced it.
    retuned = await train(
        corpus, artifacts, config=replace(TEST_CONFIG, n_trees=TEST_CONFIG.n_trees + 1)
    )
    assert retuned.version.artifact_digest != first.version.artifact_digest
    assert retuned.version.contract_id != first.version.contract_id
    assert retuned.snapshot.contract_id == first.snapshot.contract_id

    # A different seed is a different *split* over the same evidence, and §10.1 derives a
    # snapshot's id from the evidence rather than from the split — so the second cut would
    # claim the first cut's id with a different holdout group. Refused by name.
    with pytest.raises(SnapshotConflictError, match="different split"):
        await train(corpus, artifacts, seed=SEED + 1)


async def test_too_little_evidence_is_refused(tmp_path: Path) -> None:
    """Two shortages, two named refusals, and no candidate written either time.

    The counts are the documented minimums rather than statistical thresholds — below them
    the *procedure* cannot run, because one of the five split groups would be empty or a
    calibration report would describe four rows — and the refusal says which one is missing.
    """

    artifacts = ArtifactStore(tmp_path / "artifacts")

    thin = await setup_corpus(projects=5, per_project=2)
    with pytest.raises(TrainingDataError, match="eligible experience records"):
        await train(thin, artifacts)

    narrow = await setup_corpus(projects=4, per_project=6)
    with pytest.raises(TrainingDataError, match="projects"):
        await train(narrow, artifacts)

    for corpus in (thin, narrow):
        assert (
            await corpus.store.list_router_model_versions(workspace_id=corpus.workspace_id) == []
        )
        assert (
            await corpus.store.list_router_training_snapshots(workspace_id=corpus.workspace_id)
            == []
        )


async def test_the_artifact_digest_is_verified_on_load(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Bytes that no longer hash to their name are somebody else's model wearing this label.

    The tamper happens in a *copy* of the artefact tree, so the shared training run stays
    intact and this test cannot pass by breaking another one.
    """

    corpus, artifacts, trained, _ = await trained_once(tmp_path_factory)
    copied = ArtifactStore(tmp_path / "copied-artifacts")
    for digest in (
        trained.version.artifact_digest,
        trained.version.calibration_artifact_digest,
        trained.version.labels[CALIBRATION_REPORT_LABEL],
        trained.version.labels[ACCEPTANCE_LABEL],
    ):
        assert copied.save(artifacts.load(digest)) == digest

    loader = LearnedPredictorLoader(corpus.store, copied)
    assert await loader.load(trained.version.contract_id)

    target = copied.path_for(trained.version.artifact_digest)
    payload = json.loads(target.read_bytes())
    payload["seed"] = payload["seed"] + 1
    target.write_bytes(canonical_json(payload))

    with pytest.raises(ArtifactDigestMismatchError, match=trained.version.artifact_digest):
        await loader.load(trained.version.contract_id)

    # The digest is checked before anything is parsed, so a truncated file is refused with
    # the same error rather than as a JSON syntax problem.
    target.write_bytes(b"{")
    with pytest.raises(ArtifactDigestMismatchError):
        await loader.load(trained.version.contract_id)


async def test_the_holdout_evaluation_is_scored_with_the_calibrated_conformal_bound(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The quantile the holdout was scored against is the one the calibration split fitted.

    Split conformal only means anything when the quantile comes from one set of projects and
    the coverage is measured on another. A trainer that refitted the quantile on the holdout
    would report its own training coverage under a held-out name, and every number here would
    still look plausible.
    """

    corpus, artifacts, trained, _ = await trained_once(tmp_path_factory)
    version = await corpus.store.get_router_model_version(trained.version.contract_id)
    assert version is not None

    loader = LearnedPredictorLoader(corpus.store, artifacts)
    report = loader.read_calibration_report(version)
    holdout = loader.read_holdout(version)

    assert holdout.conformal_quantile == report.conformal_quantile
    assert holdout.alpha == report.alpha == TEST_CONFIG.alpha
    assert report.feature_schema_version == version.feature_schema_version
    assert holdout.verified_success_lcb == max(
        0.0, holdout.observed_verified_success_rate - holdout.conformal_quantile
    )

    # A predictor assembled from the stored bytes quotes the same bound the version promised.
    predictor = await loader.load(version.contract_id)
    assert predictor.conformal_quantile == report.conformal_quantile
    assert predictor.confidence == pytest.approx(1.0 - report.alpha)


async def test_a_training_run_with_a_run_context_records_the_candidate_trained_event(
    tmp_path: Path,
) -> None:
    """§12's ``router.candidate.trained`` is emitted when there is a run to emit it against.

    The event store is run-scoped end to end — ``AgentEvent`` requires a ``run_id`` and the
    Postgres store locks that run row — so an offline job with no run emits nothing rather
    than inventing a run that never executed. Both halves are pinned here, because "emits
    nothing" is otherwise indistinguishable from "forgot to emit".
    """

    from accretion.contracts import EventType, Run, RunState, Task, TaskEnvelope

    corpus = await setup_corpus()
    artifacts = ArtifactStore(tmp_path / "artifacts")

    quiet = await train(corpus, artifacts)
    assert quiet.version.labels[ACCEPTANCE_LABEL]

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=corpus.project_ids[0],
            objective="Train a router candidate from inside a run.",
            task_type=TaskType.IMPLEMENT,
        )
    )
    await corpus.store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=corpus.project_ids[0],
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    await corpus.store.create_run(run)

    assert await corpus.store.list_events(run.run_id) == []
    # Same seed and same config: the version is the one the quiet run already wrote, so the
    # only difference between the two calls is that this one has a run to announce into.
    loud = await train(corpus, artifacts, run_id=run.run_id)
    assert loud.version.contract_id == quiet.version.contract_id

    events = await corpus.store.list_events(run.run_id)
    assert [event.normalized_type for event in events] == [EventType.ROUTER_CANDIDATE_TRAINED]
    payload = events[0].payload
    assert payload["candidate_version_id"] == loud.version.contract_id
    assert payload["training_snapshot_id"] == loud.snapshot.contract_id
    assert payload[ACCEPTANCE_LABEL] == loud.holdout.digest()
    # §12: events carry no secrets and no hidden payloads — four scalar references only.
    assert set(payload) == {
        "candidate_version_id",
        "training_snapshot_id",
        ACCEPTANCE_LABEL,
        CALIBRATION_REPORT_LABEL,
    }


async def test_an_unknown_version_id_is_a_missing_resource_and_not_a_refusal(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A name nobody minted is a 404, not a policy answer.

    Collapsing the two would let a caller probe which version ids exist by reading the
    refusal, and would report "this model was never evaluated" about a model that was never
    created.
    """

    corpus, artifacts, _, _ = await trained_once(tmp_path_factory)
    loader = LearnedPredictorLoader(corpus.store, artifacts)
    missing = new_id("router_model_version")
    with pytest.raises(KeyError):
        await loader.load(missing)


async def test_the_training_table_keeps_decimal_costs_and_imputes_only_missing_quality(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The snapshot the candidate cites materialises back to the rows it was fitted on.

    §10.1's reproducibility claim, stated as an equality: the manifest digest recomputed from
    the store equals the one the snapshot published, and the costs that fed it are still the
    exact decimals the outcomes recorded rather than floats that have been through a round
    trip.
    """

    from accretion.routing.training_snapshot import materialize

    corpus, _, trained, _ = await trained_once(tmp_path_factory)
    snapshot = await corpus.store.get_router_training_snapshot(trained.snapshot.contract_id)
    assert snapshot is not None

    table = await materialize(snapshot, corpus.store, VOCABULARY)
    assert table.manifest_digest == snapshot.labels["manifest_digest"]
    assert len(table.rows) == int(snapshot.labels["row_count"])
    assert all(isinstance(row.cost, Decimal) for row in table.rows)
    assert {row.cost for row in table.rows} == {Decimal("2.50"), Decimal("4.50")}
    assert set(table.labels_local) == {1.0}
    assert set(table.labels_final) == {0.0, 1.0}
