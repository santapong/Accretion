"""Offline training: a CANDIDATE router version, and the evaluation without which it cannot load.

SDD §10.1–10.2 and AC4-M4-016 say one thing in two places: **offline ranking precedes any
shadow or live learned policy**. This module is where that becomes mechanical rather than
aspirational. :class:`RouterTrainingService` produces a ``CANDIDATE``
:class:`~accretion.contracts.routing.RouterModelVersion` only alongside a sealed training
snapshot, a project-disjoint holdout evaluation and a calibration report; and
:class:`LearnedPredictorLoader` — the *only* way a learned predictor enters routing, which
M2.2's dispatch and M6's shadow evaluator both call — refuses any version that cannot show
that evaluation on record.

**Why the loader is the enforcement point and not the trainer.** A trainer that always
wrote an evaluation would be a convention: the next caller to build a
:class:`RouterModelVersion` by hand, or to restore one from a backup taken mid-write, would
route with an unevaluated model and nothing would notice. The refusal therefore lives on
the read path, where every consumer must pass, and it is keyed on a label that only a
completed evaluation can produce — ``holdout_eval_digest`` is the SHA-256 of the canonical
JSON of the evaluation document, and the document is stored under that digest, so the label
cannot be forged by writing a string.

**Where the split comes from, and why it is asked for twice.** ``split.py`` allocates whole
project lineages to the five required splits, and
:meth:`~accretion.routing.training_snapshot.SnapshotBuilder.build` takes the sealed
three-group split as an *argument* — it does not invent one. So the trainer must know which
projects have eligible evidence before it can cut the snapshot that names them, which is why
the eligibility question is asked once here and once inside the builder. The alternative was
to have the builder choose a split, which would have made the snapshot's identity depend on a
policy the snapshot does not record.

**Which head carries the labels.** A record is only ``eligible_for_learning`` when its own
verification passed (``ExperienceRecord`` enforces it), so inside a snapshot every
*local* label is 1.0 and the node-success head sees no variation at all. The label variation
lives in ``final_run_status``: a node that passed locally inside a run that later failed is
exactly the disagreement §14.3 exists to catch. Calibration, the conformal quantile and every
holdout statistic here are therefore computed on the **run**-verified-success head, and the
node head is fitted from the same rows without being the thing measured. That is a property
of the sealed M3 projection rather than a choice made here, and it is stated out loud
because a calibration report that quietly measured an all-ones head would look excellent and
mean nothing.

**Leakage control in the features themselves.** Each training row is featurized with an
evidence summary built only from records of the *same project* created *strictly before* it.
Summarising over the whole snapshot would put a holdout project's outcomes inside a training
row's feature vector, and the resulting holdout would measure memorisation through the
back door while every project-disjointness assertion still passed.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import EventType, PrincipalRef, Provider, StrictModel
from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import (
    FEATURE_SCHEMA_VERSION,
    ContractSignature,
    ExecutionConfiguration,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RouterTrainingSnapshot,
    RoutingContext,
    UtilityWeights,
)
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.calibration import (
    DEFAULT_ALPHA,
    DEFAULT_BINS,
    DEFAULT_BOOTSTRAPS,
    CalibrationReport,
    Calibrator,
    CohortCalibration,
    IsotonicCalibrator,
    PlattCalibrator,
    brier,
    build_calibration_report,
    calibrator_from_json,
    conformal_quantile,
    ece,
    lcb,
    success_residuals,
)
from accretion.routing.features import EvidenceSummary, featurize, summarize_evidence
from accretion.routing.ranker import (
    DEFAULT_BAGS,
    ORDERED_HEADS,
    LearnedOutcomePredictor,
    OutcomeHead,
    RankerArtifact,
    artifact_bytes,
    train_ranker,
)
from accretion.routing.split import (
    DEFAULT_FRACTIONS,
    SplitAssignment,
    SplitFractions,
    SplitName,
    assert_disjoint,
    assign,
)
from accretion.routing.training_snapshot import (
    SnapshotBuilder,
    SnapshotRules,
    TrainingTable,
    materialize,
)
from accretion.runtimes.common import make_event

ALGORITHM_ID = "accretion.gbdt-bagged-five-head-v1"
"""What ``RouterModelVersion.algorithm_id`` records: the in-repo learner of ADR4-M4-001."""

MINIMUM_ELIGIBLE_RECORDS = 20
"""The fewest eligible experience records a candidate may be trained on.

Not a statistical threshold — no honest one exists at this scale — but the point below which
the *procedure* stops being executable: five project groups, each of which must contribute
enough rows to fit, calibrate and be scored on. Refusing here names the reason; letting it
through produces a model whose calibration report is a description of four rows.
"""

MINIMUM_PROJECTS = 5
"""One lineage per required split (``split.SPLIT_ORDER``), which is the fewest that fills them.

Below five, :func:`~accretion.routing.split._quotas` cannot give every split a root and one
of the five silently disappears while the assignment still validates.
"""

MINIMUM_ROWS_PER_GROUP = 4
"""The fewest labelled rows in each of training, calibration and holdout."""

ACCEPTANCE_LABEL = "holdout_eval_digest"
"""The label AC4-M4-016 is keyed on. Absent means the version was never evaluated."""

CALIBRATION_REPORT_LABEL = "calibration_report_digest"
SPLIT_ASSIGNMENT_LABEL = "split_assignment_digest"
IDEMPOTENCY_LABEL = "idempotency_key"

LOADABLE_STATUSES: frozenset[RouterStatus] = frozenset(
    {RouterStatus.CANDIDATE, RouterStatus.SHADOW, RouterStatus.ACTIVE}
)
"""The three lifecycle states a predictor may be assembled from.

``RETIRED`` and ``ROLLED_BACK`` are rollback *targets* (§10.3): their bytes must survive, and
loading one for routing would silently undo the rollback that retired it.
"""

ISOTONIC_MINIMUM_ROWS = 500
"""M4.2's rule: isotonic regression only where there are enough rows to shape a step function."""

_ACCEPTANCE_FLOOR_FALLBACK = 0.5
"""Used only when a joined row's objective declares a zero success floor."""


class RouterTrainingError(RuntimeError):
    """Base class for every refusal in this module."""


class TrainingDataError(RouterTrainingError):
    """There is not enough eligible evidence to execute the training procedure.

    Distinct from "the model was bad": nothing was fitted, nothing was scored, and no
    version exists. A caller that saw a candidate here would be holding a model trained on
    a handful of rows with a calibration report that could not be wrong.
    """


class SnapshotConflictError(RouterTrainingError):
    """This evidence has already been cut into a snapshot under a different split.

    :func:`~accretion.routing.training_snapshot._derived_id` derives a snapshot's identity
    from the workspace, the window, the declared rules and the *evidence* — deliberately, so
    that rebuilding one is a byte-identical re-put rather than a second row claiming to be a
    different snapshot of the same records. The split is not part of that identity, because
    it is a function of the evidence given a seed and fractions. Change the seed and the
    identity does not move while the body does, and the append-only store refuses the write
    with nothing to say about why.

    Refusing here says why. Two snapshots over one set of records with two different holdout
    groups are two different claims about what was held out, and the second one would
    overwrite the first's meaning while reusing its id. Retraining under a different seed
    therefore needs a different window or new evidence.
    """


class RouterNotEvaluatedError(RouterTrainingError):
    """A router version was asked to load without a holdout evaluation on record.

    This is AC4-M4-016. The refusal covers three cases and all three mean the same thing:
    the version carries no ``holdout_eval_digest``, or no ``calibration_report_digest``, or
    it is in a status (``RETIRED``, ``ROLLED_BACK``) that is not routable at all.
    """


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    """The learner's knobs, hashed into the version so a rerun can be told from a retune.

    Defaults are the ranker's own (:data:`~accretion.routing.ranker.DEFAULT_BAGS`, sixty
    trees, depth three): offline training happens once per version and the honest tradeoff at
    that cadence is accuracy, not minutes. Tests pass a smaller config, which is why this is
    an argument and not a constant.
    """

    n_trees: int = 60
    max_depth: int = 3
    learning_rate: float = 0.1
    l2: float = 1.0
    feature_subsample: float = 0.8
    bags: int = DEFAULT_BAGS
    alpha: float = DEFAULT_ALPHA
    bins: int = DEFAULT_BINS
    bootstraps: int = DEFAULT_BOOTSTRAPS

    def digest(self) -> str:
        """The digest of the declared configuration, recorded on the version's labels."""

        return hashlib.sha256(
            canonical_json(
                {
                    "alpha": self.alpha,
                    "bags": self.bags,
                    "bins": self.bins,
                    "bootstraps": self.bootstraps,
                    "feature_subsample": self.feature_subsample,
                    "l2": self.l2,
                    "learning_rate": self.learning_rate,
                    "max_depth": self.max_depth,
                    "n_trees": self.n_trees,
                }
            )
        ).hexdigest()


DEFAULT_TRAINING_CONFIG = TrainingConfig()


class HoldoutEvaluation(StrictModel):
    """What the candidate scored on projects it was never fitted or calibrated on.

    Sealed as a document rather than returned as numbers because its digest is the label the
    loader gates on: a claim that can be recomputed from stored bytes is falsifiable, and a
    number passed between two functions is not.

    Every rate here is measured on the **run**-verified-success head (see the module
    docstring). ``ranking_concordance`` and ``baseline_ranking_concordance`` are the same
    statistic computed two ways — the model's calibrated probability, and the deterministic
    §9.4 cold-start utility the router would have ranked on without any model — so their
    difference is the only "did learning help" claim this milestone is entitled to make.
    ``None`` means the holdout contained no comparable pair, which is a real answer and not a
    zero.
    """

    schema_version: Literal["1.0"] = "1.0"
    training_snapshot_id: str = Field(min_length=1, max_length=64)
    feature_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    alpha: float = Field(gt=0, lt=1)
    conformal_quantile: float = Field(ge=0, le=1)
    seed: int = Field(ge=0)
    n_rows: int = Field(ge=1)
    project_ids: list[str] = Field(min_length=1, max_length=4_096)
    observed_verified_success_rate: float = Field(ge=0, le=1)
    verified_success_lcb: float = Field(ge=0, le=1)
    ece_10bin: float = Field(ge=0, le=1)
    brier: float = Field(ge=0, le=1)
    accepted_count: int = Field(ge=0)
    false_acceptance_rate: float = Field(ge=0, le=1)
    ranking_concordance: float | None = Field(default=None, ge=0, le=1)
    baseline_ranking_concordance: float | None = Field(default=None, ge=0, le=1)
    per_cohort: list[CohortCalibration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _projects_and_cohorts_are_ordered(self) -> Self:
        if sorted(self.project_ids) != self.project_ids or len(set(self.project_ids)) != len(
            self.project_ids
        ):
            raise ValueError(
                "holdout project ids must be ascending and unique; an evaluation whose "
                "project list depended on iteration order would not have a stable digest"
            )
        cohorts = [item.cohort_id for item in self.per_cohort]
        if cohorts != sorted(cohorts) or len(set(cohorts)) != len(cohorts):
            raise ValueError("per-cohort entries must be ascending and unique by cohort_id")
        return self

    @property
    def n_projects(self) -> int:
        return len(self.project_ids)

    @property
    def ranking_gain(self) -> float | None:
        """How much the model's ordering beat the deterministic baseline's, or ``None``."""

        if self.ranking_concordance is None or self.baseline_ranking_concordance is None:
            return None
        return self.ranking_concordance - self.baseline_ranking_concordance

    def digest(self) -> str:
        """SHA-256 over this document's canonical JSON: the value of ``holdout_eval_digest``."""

        return hashlib.sha256(canonical_json(self)).hexdigest()


@dataclass(frozen=True, slots=True)
class TrainedCandidate:
    """Everything one training run produced, in the order it was produced.

    The snapshot and the two evaluation documents are returned beside the version rather than
    left to be re-read, because a caller that had to re-read them could not tell a version
    written by this run from one that already existed under the same derived id.
    """

    version: RouterModelVersion
    snapshot: RouterTrainingSnapshot
    holdout: HoldoutEvaluation
    calibration: CalibrationReport
    assignment: SplitAssignment


@dataclass(frozen=True, slots=True)
class _JoinedRow:
    """One training row with the five sealed contracts it was a decision about.

    A :class:`~accretion.routing.training_snapshot.TrainingRow` is a projection of an
    *outcome*; a feature vector needs the *decision* — the routing context, the candidate
    configuration, the node contract and the objective. Joining them is the only work in this
    module that can fail for a reason that is nobody's mistake (a record whose decision was
    never persisted), which is why unjoinable rows are counted rather than raised on.
    """

    experience_id: str
    project_id: str
    values: list[float | None]
    label: float
    quality: float | None
    cost: float
    latency_ms: float
    weight: float
    node_kind: str
    risk_class: str
    provider: str
    signature_key: str
    verified_success_floor: float
    baseline_utility: float


class RouterTrainingService:
    """Fits a candidate router and records what it is allowed to claim.

    Holds a store, an artefact store and a clock, and nothing about one training run: the
    window, the seed, the principal and the split fractions are all arguments, so two runs
    given the same arguments produce the same documents no matter which service object made
    them.

    ``clock`` is injected for the reason
    :meth:`~accretion.routing.training_snapshot.SnapshotBuilder.build` documents — the
    snapshot's ``created_at`` is inside its ``content_hash``, so reproducibility is only
    testable when the instant is.
    """

    def __init__(
        self,
        store: StateStore,
        artifacts: ArtifactStore,
        *,
        clock: Callable[[], datetime],
        config: TrainingConfig = DEFAULT_TRAINING_CONFIG,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.clock = clock
        self.config = config

    async def train_candidate(
        self,
        *,
        workspace_id: str,
        window: tuple[datetime, datetime],
        seed: int,
        created_by: PrincipalRef,
        split_fractions: SplitFractions | None = None,
        rules: SnapshotRules | None = None,
        parent_version_id: str | None = None,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> TrainedCandidate:
        """Fit, calibrate, evaluate and record one CANDIDATE version.

        The order is load-bearing and is asserted by the tests: the snapshot is written
        **before** the version, because a version that named a snapshot which did not yet
        exist would be, for the width of that window, a router pointing at evidence nobody
        could read.

        Raises :class:`TrainingDataError` when the workspace cannot supply the documented
        minimum of evidence, and lets
        :class:`~accretion.routing.split.SplitViolation` through unchanged when the
        assignment leaks — a leaking split is not a data shortage and must not be reported
        as one.
        """

        fractions = split_fractions if split_fractions is not None else DEFAULT_FRACTIONS
        snapshot_rules = rules if rules is not None else SnapshotRules.over()

        records = await self._eligible_records(workspace_id, window, snapshot_rules)
        project_ids = sorted({record.project_id for record in records if record.project_id})
        if len(records) < MINIMUM_ELIGIBLE_RECORDS:
            raise TrainingDataError(
                f"workspace {workspace_id} has {len(records)} eligible experience records "
                f"in the window, fewer than the {MINIMUM_ELIGIBLE_RECORDS} a training run "
                "needs to fit, calibrate and score on disjoint projects"
            )
        if len(project_ids) < MINIMUM_PROJECTS:
            raise TrainingDataError(
                f"workspace {workspace_id} has eligible evidence from {len(project_ids)} "
                f"projects, fewer than the {MINIMUM_PROJECTS} required to fill the five "
                "split groups without putting one project on both sides"
            )

        assignment = assign(
            {project_id: project_id for project_id in project_ids},
            fractions=fractions,
            seed=seed,
        )
        assert_disjoint(assignment)
        snapshot = await SnapshotBuilder(self.store).build(
            workspace_id=workspace_id,
            window=window,
            split=assignment.to_sealed(),
            rules=snapshot_rules,
            created_by=created_by,
            clock=self.clock,
        )
        snapshot = await self._reconcile_snapshot(snapshot)
        table = await materialize(snapshot, self.store, snapshot_rules.vocabulary)
        joined = await self._join(workspace_id, table, snapshot_rules)

        groups = {
            name: [
                row
                for row in joined
                if row.project_id in set(assignment.project_ids_for(name))
            ]
            for name in (SplitName.TRAIN, SplitName.CALIBRATION)
        }
        holdout_projects = set(assignment.project_ids_for(SplitName.TEST)) | set(
            assignment.project_ids_for(SplitName.DRIFT)
        )
        groups[SplitName.TEST] = [row for row in joined if row.project_id in holdout_projects]
        for name, rows in groups.items():
            if len(rows) < MINIMUM_ROWS_PER_GROUP:
                raise TrainingDataError(
                    f"the {name.value} group has {len(rows)} labelled, joinable rows, fewer "
                    f"than the {MINIMUM_ROWS_PER_GROUP} required; {len(joined)} of "
                    f"{len(table.rows)} snapshot rows carried a final run label and a "
                    "persisted routing decision to featurize against"
                )

        training_rows = groups[SplitName.TRAIN]
        calibration_rows = groups[SplitName.CALIBRATION]
        holdout_rows = groups[SplitName.TEST]

        artifact = self._fit(training_rows, seed=seed)
        calibrator = self._calibrate(artifact, calibration_rows)
        quantile, report = self._calibration_report(
            artifact, calibrator, calibration_rows, seed=seed
        )
        predictor = LearnedOutcomePredictor(
            artifact=artifact,
            calibrator=calibrator,
            conformal_quantile=quantile,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            alpha=self.config.alpha,
        )
        holdout = self._evaluate(
            predictor,
            holdout_rows,
            snapshot_id=snapshot.contract_id,
            quantile=quantile,
            seed=seed,
        )

        artifact_digest = self.artifacts.save(artifact_bytes(artifact))
        calibration_digest = self.artifacts.save(canonical_json(calibrator.to_json()))
        report_digest = self.artifacts.save(canonical_json(report))
        holdout_digest = self.artifacts.save(canonical_json(holdout))
        assignment_digest = self.artifacts.save(canonical_json(assignment))

        labels = {
            ACCEPTANCE_LABEL: holdout_digest,
            CALIBRATION_REPORT_LABEL: report_digest,
            SPLIT_ASSIGNMENT_LABEL: assignment_digest,
            "calibration_row_count": str(len(calibration_rows)),
            "holdout_project_count": str(holdout.n_projects),
            "holdout_row_count": str(len(holdout_rows)),
            "seed": str(seed),
            "training_config_digest": self.config.digest(),
            "training_row_count": str(len(training_rows)),
        }
        if idempotency_key is not None:
            labels[IDEMPOTENCY_LABEL] = idempotency_key

        version_id = derived_id(
            "router_model_version",
            workspace_id,
            snapshot.contract_id,
            ALGORITHM_ID,
            FEATURE_SCHEMA_VERSION,
            artifact_digest,
            calibration_digest,
            report_digest,
            holdout_digest,
            str(seed),
            idempotency_key or "",
        )
        # Built through `model_validate` for the reason `SnapshotBuilder.build` gives: the
        # pydantic mypy plugin does not carry `CanonicalContract`'s header fields onto a
        # subclass declared in another module, and the blanket ignore the keyword
        # constructor would need would also hide a misspelled field.
        version = RouterModelVersion.model_validate(
            {
                "contract_id": version_id,
                "created_at": self.clock(),
                "created_by": created_by,
                "workspace_id": workspace_id,
                "scope": RouterScope.TEAM_WORKSPACE,
                "algorithm_id": ALGORITHM_ID,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "training_snapshot_id": snapshot.contract_id,
                "artifact_digest": artifact_digest,
                "calibration_artifact_digest": calibration_digest,
                "parent_version_id": parent_version_id,
                "status": RouterStatus.CANDIDATE,
                "labels": labels,
            }
        )

        stored_snapshot = await self.store.put_router_training_snapshot(snapshot)
        stored_version = await self.store.put_router_model_version(version)
        await self._announce(stored_version, stored_snapshot, run_id=run_id)
        return TrainedCandidate(
            version=stored_version,
            snapshot=stored_snapshot,
            holdout=holdout,
            calibration=report,
            assignment=assignment,
        )

    async def _reconcile_snapshot(
        self, snapshot: RouterTrainingSnapshot
    ) -> RouterTrainingSnapshot:
        """Prefer the stored cut over the rebuilt one when they name the same evidence.

        A snapshot's id covers the evidence but not the instant it was cut, so a second
        training run over an unchanged window rebuilds the same id with a later
        ``created_at`` — and the append-only store would refuse that as an immutability
        violation even though nothing about the evidence had changed. Taking the stored
        document makes retraining over a settled window work, and keeps the first cut's
        timestamp, which is the honest one: that is when this evidence was gathered.

        A stored snapshot whose split or manifest differs is not the same claim and is
        refused by name.
        """

        existing = await self.store.get_router_training_snapshot(snapshot.contract_id)
        if existing is None:
            return snapshot
        if existing.split != snapshot.split or existing.labels.get(
            "manifest_digest"
        ) != snapshot.labels.get("manifest_digest"):
            raise SnapshotConflictError(
                f"training snapshot {snapshot.contract_id} already covers this workspace, "
                "window and evidence under a different split or manifest; a re-split of the "
                "same records is a second claim about what was held out and needs its own "
                "window or new evidence"
            )
        return existing

    # ------------------------------------------------------------------ evidence

    async def _eligible_records(
        self,
        workspace_id: str,
        window: tuple[datetime, datetime],
        rules: SnapshotRules,
    ) -> list[ExperienceRecord]:
        """The records the snapshot builder will include, asked before it is built.

        Deliberately the same question the builder asks, because the builder takes the split
        as an argument and the split is a function of which projects have evidence. The
        alternative — a builder that chose its own split — would have made a snapshot's
        identity depend on a policy the snapshot does not record.
        """

        window_start, window_end = window
        excluded = set(rules.excluded_contradiction_statuses)
        eligible: list[ExperienceRecord] = []
        for record in await self.store.list_experience_records(workspace_id=workspace_id):
            if not window_start <= record.created_at < window_end:
                continue
            if not record.eligible_for_learning or record.contradiction_status in excluded:
                continue
            experience = await self.store.get_experience(record.contract_id)
            if experience is None or experience.retracted:
                continue
            eligible.append(record)
        return eligible

    async def _join(
        self,
        workspace_id: str,
        table: TrainingTable,
        rules: SnapshotRules,
    ) -> list[_JoinedRow]:
        """Featurize every snapshot row that still has the decision it was an outcome of."""

        records = {
            record.contract_id: record
            for record in await self.store.list_experience_records(workspace_id=workspace_id)
        }
        nodes: dict[str, NodeContract] = {
            node.execution_instance_id: node
            for node in await self.store.list_node_contracts(workspace_id=workspace_id)
        }
        contexts: dict[str, RoutingContext] = {}
        for request in await self.store.list_routing_requests(workspace_id=workspace_id):
            contexts.setdefault(request.node_contract_ref.node_contract_id, request)
        configurations: dict[tuple[str, str], ExecutionConfiguration] = {}
        for candidate in await self.store.list_configuration_candidates(
            workspace_id=workspace_id
        ):
            key = (candidate.routing_request_id, candidate.configuration.configuration_hash)
            configurations.setdefault(key, candidate.configuration)
        objectives: dict[str, ObjectiveContract] = {}

        by_project: dict[str, list[ExperienceRecord]] = {}
        for known in records.values():
            if known.project_id is not None:
                by_project.setdefault(known.project_id, []).append(known)

        joined: list[_JoinedRow] = []
        for row, label, weight in zip(
            table.rows, table.labels_final, table.weights, strict=True
        ):
            if label is None or row.project_id is None:
                continue
            record = records.get(row.experience_id)
            node = nodes.get(row.source_node_execution_id)
            if record is None or node is None:
                continue
            context = contexts.get(node.contract_id)
            if context is None:
                continue
            configuration = configurations.get((context.contract_id, row.configuration_hash))
            if configuration is None:
                continue
            reference = node.objective_contract_ref
            if reference is None:
                continue
            objective = objectives.get(reference.objective_contract_id)
            if objective is None:
                found = await self.store.get_objective_contract(reference.objective_contract_id)
                if found is None:
                    continue
                objectives[reference.objective_contract_id] = found
                objective = found
            evidence = self._evidence_for(record, by_project.get(record.project_id or "", []))
            features = featurize(
                context, configuration, node, objective, evidence, rules.vocabulary
            )
            joined.append(
                _JoinedRow(
                    experience_id=row.experience_id,
                    project_id=row.project_id,
                    values=list(features.values),
                    label=float(label),
                    quality=row.quality,
                    cost=float(row.cost),
                    latency_ms=float(row.latency_ms),
                    weight=weight,
                    node_kind=row.node_kind,
                    risk_class=row.risk_class,
                    provider=row.provider,
                    signature_key=_signature_key(record.contract_signature),
                    verified_success_floor=objective.verified_success_floor,
                    baseline_utility=_baseline_utility(evidence, objective.utility_weights),
                )
            )
        return joined

    @staticmethod
    def _evidence_for(
        record: ExperienceRecord, siblings: Sequence[ExperienceRecord]
    ) -> EvidenceSummary:
        """What was knowable about this configuration when this decision was made.

        Same project, strictly earlier. Both halves matter: later records would let an
        outcome predict itself, and other projects' records would carry a holdout project's
        outcomes into a training row's features while every project-disjointness check
        still passed.
        """

        earlier = [
            sibling
            for sibling in siblings
            if sibling.created_at < record.created_at and sibling.contract_id != record.contract_id
        ]
        return summarize_evidence(
            earlier,
            signature=record.contract_signature,
            configuration_hash=record.configuration_hash,
            as_of=record.created_at,
        )

    # ------------------------------------------------------------------ fitting

    def _fit(self, rows: Sequence[_JoinedRow], *, seed: int) -> RankerArtifact:
        """Fit all five heads on the training projects.

        ``quality`` is imputed with the training mean where a record never measured one, and
        that is a real choice with a real cost: it teaches the quality head that an
        unmeasured node is an average node. The alternative — dropping those rows — would
        have thrown away their success labels too, which are the scarce quantity here.
        """

        qualities = [row.quality for row in rows if row.quality is not None]
        mean_quality = math.fsum(qualities) / len(qualities) if qualities else 0.0
        targets: dict[OutcomeHead, list[float]] = {
            OutcomeHead.NODE_VERIFIED_SUCCESS: [1.0 for _ in rows],
            OutcomeHead.RUN_VERIFIED_SUCCESS: [row.label for row in rows],
            OutcomeHead.QUALITY: [
                mean_quality if row.quality is None else row.quality for row in rows
            ],
            OutcomeHead.COST: [row.cost for row in rows],
            OutcomeHead.LATENCY: [row.latency_ms for row in rows],
        }
        return train_ranker(
            [row.values for row in rows],
            {head: targets[head] for head in ORDERED_HEADS},
            version_id=ALGORITHM_ID,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            seed=seed,
            n_trees=self.config.n_trees,
            max_depth=self.config.max_depth,
            learning_rate=self.config.learning_rate,
            l2=self.config.l2,
            feature_subsample=self.config.feature_subsample,
            bags=self.config.bags,
        )

    @staticmethod
    def _margins(artifact: RankerArtifact, rows: Sequence[_JoinedRow]) -> list[float]:
        """The bagged *uncalibrated* score per row: what a calibrator is fitted against."""

        ensemble = artifact.ensemble(OutcomeHead.RUN_VERIFIED_SUCCESS)
        return [
            math.fsum(ensemble.bag_values(row.values, None))
            / len(ensemble.models)
            for row in rows
        ]

    def _calibrate(
        self, artifact: RankerArtifact, rows: Sequence[_JoinedRow]
    ) -> Calibrator:
        """Fit the probability calibrator on the calibration projects and nowhere else."""

        margins = self._margins(artifact, rows)
        labels = [row.label for row in rows]
        if len(rows) >= ISOTONIC_MINIMUM_ROWS:
            return IsotonicCalibrator.fit(margins, labels)
        return PlattCalibrator.fit(margins, labels)

    def _calibration_report(
        self,
        artifact: RankerArtifact,
        calibrator: Calibrator,
        rows: Sequence[_JoinedRow],
        *,
        seed: int,
    ) -> tuple[float, CalibrationReport]:
        """The conformal quantile and the report, both from the calibration split.

        ``build_calibration_report`` is called with ``conformal_quantile_value=None``, which
        is its documented "this *is* the calibration split" mode: the quantile is taken from
        these rows because these are the rows it is a promise about. The holdout's coverage
        is measured separately, against this quantile, in :meth:`_evaluate`.
        """

        probabilities = [
            calibrator.apply(margin) for margin in self._margins(artifact, rows)
        ]
        labels = [row.label for row in rows]
        groups = [row.project_id for row in rows]
        report = build_calibration_report(
            probabilities=probabilities,
            labels=labels,
            groups=groups,
            method=calibrator.method,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            seed=seed,
            cohorts=[_cohort_id("RISK", row.risk_class) for row in rows],
            alpha=self.config.alpha,
            bins=self.config.bins,
            bootstraps=self.config.bootstraps,
        )
        quantile = conformal_quantile(
            success_residuals(probabilities, labels), self.config.alpha, groups
        )
        return quantile, report

    def _evaluate(
        self,
        predictor: LearnedOutcomePredictor,
        rows: Sequence[_JoinedRow],
        *,
        snapshot_id: str,
        quantile: float,
        seed: int,
    ) -> HoldoutEvaluation:
        """Score the fitted, calibrated predictor on projects it has never seen."""

        probabilities: list[float] = []
        lower_bounds: list[float] = []
        for row in rows:
            predicted, _ = predictor.predict(row.values)
            probabilities.append(predicted.run_verified_success.mean)
            lower_bounds.append(predicted.run_verified_success.lower_bound)
        labels = [row.label for row in rows]
        observed = math.fsum(labels) / len(labels)

        accepted = [
            index
            for index, row in enumerate(rows)
            if lower_bounds[index]
            >= (row.verified_success_floor or _ACCEPTANCE_FLOOR_FALLBACK)
        ]
        false_accepted = [index for index in accepted if labels[index] == 0.0]

        cohorts: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            cohorts.setdefault(_cohort_id("RISK", row.risk_class), []).append(index)
            cohorts.setdefault(_cohort_id("PROVIDER", row.provider), []).append(index)
        per_cohort = [
            CohortCalibration(
                cohort_id=cohort_id,
                ece=ece(
                    [probabilities[index] for index in indexes],
                    [labels[index] for index in indexes],
                    self.config.bins,
                ),
                n=len(indexes),
            )
            for cohort_id, indexes in sorted(cohorts.items())
        ]

        keys = [row.signature_key for row in rows]
        return HoldoutEvaluation(
            training_snapshot_id=snapshot_id,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            alpha=self.config.alpha,
            conformal_quantile=quantile,
            seed=seed,
            n_rows=len(rows),
            project_ids=sorted({row.project_id for row in rows}),
            observed_verified_success_rate=observed,
            verified_success_lcb=lcb(observed, quantile),
            ece_10bin=ece(probabilities, labels, self.config.bins),
            brier=brier(probabilities, labels),
            accepted_count=len(accepted),
            false_acceptance_rate=(
                len(false_accepted) / len(accepted) if accepted else 0.0
            ),
            ranking_concordance=_concordance(probabilities, labels, keys),
            baseline_ranking_concordance=_concordance(
                [row.baseline_utility for row in rows], labels, keys
            ),
            per_cohort=per_cohort,
        )

    # ------------------------------------------------------------------ events

    async def _announce(
        self,
        version: RouterModelVersion,
        snapshot: RouterTrainingSnapshot,
        *,
        run_id: str | None,
    ) -> None:
        """Emit §12's ``router.candidate.trained`` when there is a run to emit it against.

        The event store is run-scoped end to end: ``AgentEvent`` requires a ``run_id``, and
        ``PostgresStore.append_event`` locks the run row and raises ``KeyError`` when there
        is none. Offline training has no run, so a synthesised id would either violate that
        key or invent a run that never executed. The event is therefore emitted only when a
        caller supplies a real run context — M6's shadow registration and M8's promotion
        both have one — and the durable record of an unattached training run is the version
        row itself, which carries the snapshot id and both evaluation digests.
        """

        if run_id is None:
            return
        run = await self.store.get_run(run_id)
        if run is None:
            return
        await self.store.append_event(
            make_event(
                run_id=run_id,
                session_id=run.session_id or "ses_pending",
                provider=Provider.DETERMINISTIC,
                native_type="router.candidate.trained",
                normalized_type=EventType.ROUTER_CANDIDATE_TRAINED,
                payload={
                    "candidate_version_id": version.contract_id,
                    "training_snapshot_id": snapshot.contract_id,
                    ACCEPTANCE_LABEL: version.labels[ACCEPTANCE_LABEL],
                    CALIBRATION_REPORT_LABEL: version.labels[CALIBRATION_REPORT_LABEL],
                },
                adapter_version="router-training-v1",
            )
        )


class LearnedPredictorLoader:
    """The only door a learned predictor comes through, and it is locked (AC4-M4-016).

    Every consumer — M2.2's dispatch, M6's shadow evaluator, M8's promotion gate — assembles
    its predictor here, so the refusal below is the single place "nothing learned routes
    without a holdout evaluation" has to hold. A loader that checked only ``status`` would
    pass a hand-written ``CANDIDATE`` that was never evaluated, which is precisely the
    failure the criterion names.
    """

    def __init__(self, store: StateStore, artifacts: ArtifactStore) -> None:
        self.store = store
        self.artifacts = artifacts

    async def load(self, version_id: str) -> LearnedOutcomePredictor:
        """Assemble the predictor for ``version_id``, or refuse.

        Raises ``KeyError`` for an unknown version (the API's 404 convention),
        :class:`RouterNotEvaluatedError` for a version that may not route, and
        :class:`~accretion.routing.ranker.ArtifactDigestMismatchError` when the stored bytes
        no longer hash to the digest the version pinned.
        """

        version = await self.store.get_router_model_version(version_id)
        if version is None:
            raise KeyError(version_id)
        return self.assemble(version)

    def assemble(self, version: RouterModelVersion) -> LearnedOutcomePredictor:
        """The same refusal and the same assembly, for a version already in hand."""

        self.require_evaluated(version)
        report = self.read_calibration_report(version)
        artifact = RankerArtifact.from_json(
            _decode(self.artifacts.load(version.artifact_digest))
        )
        calibrator = calibrator_from_json(
            _decode(self.artifacts.load(version.calibration_artifact_digest))
        )
        return LearnedOutcomePredictor(
            artifact=artifact,
            calibrator=calibrator,
            conformal_quantile=report.conformal_quantile,
            feature_schema_version=version.feature_schema_version,
            alpha=report.alpha,
        )

    @staticmethod
    def require_evaluated(version: RouterModelVersion) -> None:
        """Refuse a version that has no evaluation on record or no routable status."""

        if version.status not in LOADABLE_STATUSES:
            raise RouterNotEvaluatedError(
                f"router version {version.contract_id} is {version.status.value}; only "
                f"{sorted(status.value for status in LOADABLE_STATUSES)} may be loaded for "
                "routing, and a retired version is a rollback target rather than a policy"
            )
        if not version.labels.get(ACCEPTANCE_LABEL):
            raise RouterNotEvaluatedError(
                f"router version {version.contract_id} carries no {ACCEPTANCE_LABEL}: it "
                "was never evaluated on a project-disjoint holdout, and SDD §10.1-10.2 put "
                "offline ranking before any shadow or live learned policy (AC4-M4-016)"
            )
        if not version.labels.get(CALIBRATION_REPORT_LABEL):
            raise RouterNotEvaluatedError(
                f"router version {version.contract_id} carries no "
                f"{CALIBRATION_REPORT_LABEL}: without the report there is no conformal "
                "quantile, so every lower bound it produced would be uncalibrated"
            )

    def read_calibration_report(self, version: RouterModelVersion) -> CalibrationReport:
        """The stored calibration report, verified against the digest the version pinned."""

        self.require_evaluated(version)
        return CalibrationReport.model_validate(
            _decode(self.artifacts.load(version.labels[CALIBRATION_REPORT_LABEL]))
        )

    def read_holdout(self, version: RouterModelVersion) -> HoldoutEvaluation:
        """The stored holdout evaluation, verified against ``holdout_eval_digest``."""

        self.require_evaluated(version)
        return HoldoutEvaluation.model_validate(
            _decode(self.artifacts.load(version.labels[ACCEPTANCE_LABEL]))
        )


def _decode(payload: bytes) -> dict[str, Any]:
    """Parse a stored artefact, refusing anything that is not a JSON object.

    The bytes have already been rehashed by :meth:`ArtifactStore.load`, so this cannot be
    someone else's document; it can still be a document of the wrong *shape*, and every
    caller below expects a mapping.
    """

    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise RouterTrainingError(
            f"stored artefact is a {type(document).__name__}, not a JSON object"
        )
    return document


def _cohort_id(family: str, value: str) -> str:
    """A cohort name that says which family it belongs to.

    §10.2 asks for several cohort families at once — provider version, critical risk — and a
    report that reported bare values could not tell ``NORMAL`` the risk class from ``NORMAL``
    anything else.
    """

    return f"{family}:{value}"


def _signature_key(signature: ContractSignature) -> str:
    """The node class two rows must share before ranking one against the other is meaningful.

    The full §7.10 retrieval signature and not a convenient subset: two nodes with the same
    kind but different objectives, capabilities or verifiers are not competing for the same
    decision, and pairing them would report agreement with an ordering nobody was asked to
    produce.
    """

    return hashlib.sha256(canonical_json(signature)).hexdigest()[:16]


def _baseline_utility(evidence: EvidenceSummary, weights: UtilityWeights) -> float:
    """The deterministic §9.4 cold-start score: the prior, scaling the objective's utility.

    This is what the router ranks on before any model exists — the observed success rate for
    configurations like this one, times the objective's own weighting of the observed
    quality, cost and latency. An unobserved prior scores zero rather than a neutral 0.5,
    because §9.4's cold start prefers evidence it has to evidence it does not.
    """

    success = evidence.verified_success_rate or 0.0
    quality = evidence.mean_quality or 0.0
    cost = evidence.mean_cost or 0.0
    latency_seconds = (evidence.mean_latency_ms or 0.0) / 1_000.0
    return success * (
        weights.quality * quality - weights.cost * cost - weights.latency * latency_seconds
    )


def _concordance(
    scores: Sequence[float], labels: Sequence[float], groups: Sequence[str]
) -> float | None:
    """Pairwise ranking agreement within a node class: the fraction of (pass, fail) pairs
    the score ordered correctly, counting a tie as half.

    Computed *within* groups, never across them, because the router never chooses between
    two different nodes' configurations. ``None`` when the holdout contains no group holding
    both a pass and a fail — an honest "not measurable here" rather than a 0.5 that would
    read as chance-level performance.
    """

    concordant = 0.0
    comparisons = 0
    by_group: dict[str, list[int]] = {}
    for index, group in enumerate(groups):
        by_group.setdefault(group, []).append(index)
    for indexes in by_group.values():
        positives = [index for index in indexes if labels[index] == 1.0]
        negatives = [index for index in indexes if labels[index] == 0.0]
        for positive in positives:
            for negative in negatives:
                comparisons += 1
                if scores[positive] > scores[negative]:
                    concordant += 1.0
                elif scores[positive] == scores[negative]:
                    concordant += 0.5
    if comparisons == 0:
        return None
    return concordant / comparisons
