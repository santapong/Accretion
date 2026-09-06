"""Promotion and rollback: §10.3's "atomic and reversible", with the reversal proved first.

SDD §10.3 says a promotion is atomic and reversible. M0 gave it neither. Atomicity was
missing because the version rows and the record of which one is serving were separate
writes; reversibility was missing because "serving" was a mutable-looking ``status`` column
on an append-only table, so the first ``ACTIVE`` row could never be retired. ADR-061's
ledger fixes the second, :meth:`accretion.persistence.store.StateStore.activate_router_version`
fixes the first, and this module is the policy that decides when either may run.

**The rollback drill runs before anything is written, and that ordering is the claim.**
AC4-M8-038: a report whose ``rollback_target`` cannot be loaded and scored is not
promotable, and refusing it leaves no row behind. A promotion is only reversible if the
thing it would reverse *to* still works — the artefact bytes are still in the store, they
still rehash to the digest the version pinned, the calibrator still loads, the feature
schema still matches, and the assembled predictor still returns a number for a real feature
row. Every one of those can have rotted since the target was trained, and every one of them
is discovered at exactly the wrong moment if it is discovered during the incident that
needed the rollback. So :class:`RollbackDrill` performs the whole rehearsal first, and its
digest goes into the ledger entry as evidence that the rehearsal happened for *this*
promotion rather than at some earlier time.

Writing the version rows first and drilling afterwards would pass a test that only checked
the refusal, which is why the test for this asserts the ledger and the version table are
both untouched after a refusal, and not merely that an exception was raised.

**Two rows for one act.** Both promotion and rollback write two ``RouterModelVersion``
records: the row that becomes the head, and a tombstone recording what the head displaced
(``RETIRED`` after a promotion, ``ROLLED_BACK`` after a withdrawal). Nothing is updated in
place, because nothing in this family ever is; the history of the artefact is the rows, and
the history of the decisions is the ledger beside them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from hashlib import sha256
from typing import Any

from accretion.contracts import (
    EventType,
    FindingSeverity,
    PrincipalRef,
    PrincipalStatus,
    Provider,
)
from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import (
    CohortResult,
    ContradictionStatus,
    ExecutionConfiguration,
    ExperienceRecord,
    FailureType,
    MetricComparison,
    NodeContract,
    ObjectiveContract,
    RegressionFinding,
    RiskClass,
    RouterActivation,
    RouterActivationKind,
    RouterModelVersion,
    RouterPromotionDecision,
    RouterPromotionReport,
    RouterStatus,
    RouterTrainingSnapshot,
    RoutingContext,
    ShadowSummary,
    VerificationState,
)
from accretion.ids import derived_id, new_id
from accretion.persistence.store import StateStore
from accretion.routing.activation import ActivationLedger, family_key_for
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.errors import RoutingError
from accretion.routing.features import (
    EvidenceSummary,
    Vocabulary,
    featurize,
    summarize_evidence,
)
from accretion.routing.ope import (
    PromotionConfig,
    clip_mass,
    default_promotion_config,
    ess,
    lambda_rule,
    ls_estimate,
    snips,
    sup_t_band,
)
from accretion.routing.ranker import LearnedOutcomePredictor
from accretion.routing.selector import DETERMINISTIC_PROPENSITY
from accretion.routing.shadow import ShadowReportConfig, shadow_gate, shadow_report
from accretion.routing.split import (
    DEFAULT_FRACTIONS,
    SplitAssignment,
    SplitViolation,
    assert_disjoint,
)
from accretion.routing.stats import (
    Interval,
    bonferroni,
    clopper_pearson,
    hierarchical_bootstrap,
)
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    IDEMPOTENCY_LABEL,
    HoldoutEvaluation,
    LearnedPredictorLoader,
)
from accretion.routing.training_snapshot import materialize
from accretion.runtimes.common import make_event

DRILL_DIGEST_LABEL = "accretion.rollback-drill-digest"
"""The label under which a ledger entry records the drill it passed.

On the entry rather than on the version, because the drill is a property of *this
activation* — the same version drilled a month earlier says nothing about whether its bytes
are readable today, and a label on the version would have been indistinguishable from that
weaker claim.
"""

ADAPTER_VERSION = "router-promotion-v1"


class PromotionError(RoutingError):
    """A promotion or rollback this service refuses to perform.

    A :class:`~accretion.routing.errors.RoutingError` so that the HTTP adapter stays a
    projection: ``main.py``'s existing handler turns the code and status carried here into
    the ``{code, message, correlation_id, retryable}`` envelope, and no new handler and no
    new branch in ``main.py`` is needed for a refusal to reach the wire as itself rather
    than as a 500.
    """

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(code, message, status_code=status_code)


class PromotionNotApprovedError(PromotionError):
    """The evaluation did not say ``PROMOTE``, so there is nothing to activate.

    ``REQUIRE_REVIEW`` is refused here exactly as ``REJECT`` is. §10.3 makes promotion a
    human act on the strength of an evaluation, and a report that asked for review is a
    report whose review has not happened; treating it as promotable would make the third
    decision value a slower spelling of the first.
    """

    def __init__(self, report: RouterPromotionReport) -> None:
        super().__init__(
            "ROUTER_PROMOTION_NOT_APPROVED",
            f"promotion report {report.contract_id} decided "
            f"{report.decision.value}; only PROMOTE may be activated",
        )


class RollbackDrillError(PromotionError):
    """The rollback target could not be loaded and scored (AC4-M8-038).

    Carries the target's id and the underlying refusal, because the operator's next action
    depends on which one failed: a missing artefact is a storage problem, a digest mismatch
    is a corruption problem, and a feature-schema mismatch is a version that can never be
    rolled back to again and has to be superseded rather than repaired.
    """

    def __init__(self, target_version_id: str, reason: str) -> None:
        super().__init__(
            "ROUTER_ROLLBACK_DRILL_FAILED",
            f"rollback target {target_version_id} did not survive the drill: {reason}; "
            "§10.3 makes promotion reversible, and a target that cannot be loaded and "
            "scored now is a reversal that would fail during the incident that needed it",
        )


class ActivationConflictError(PromotionError):
    """The ledger does not say what the caller assumed it said.

    Raised when a rollback names a version that is not the head. Rolling back "the active
    router" is only meaningful against the activation the caller was looking at; if
    something has been promoted since, withdrawing the version they named would restore a
    router two promotions old without anyone having asked for that.
    """


class RollbackDrill:
    """Load the target, score it, and return a digest of what it predicted.

    The rehearsal is deliberately the *whole* path a router takes at routing time, not a
    liveness check: :meth:`LearnedPredictorLoader.assemble` re-reads both artefacts through
    the content-addressed store (which rehashes them), refuses a version with no holdout
    evaluation on record, and refuses a predictor whose parts disagree about the feature
    schema; then one real ``FeatureRow`` goes through :meth:`predict`. A target that passes
    all of that can serve traffic, and nothing weaker than that is worth calling a drill.

    **The probe is the committed golden fixture**, mirrored here as the four sealed
    documents ``featurize`` needs. Sealed: each one is validated with its own
    ``content_hash``, so a contract change that moved a field breaks this module loudly
    rather than silently changing what the drill measures. A synthesised probe would have
    drifted from the contracts the moment either changed, and the digest it produced would
    have gone on being comparable to its own past self while meaning something else.

    The digest commits to the version, the artefact it pinned and the five estimates the
    probe drew out of it — so two drills of the same target return the same string, and a
    target whose bytes changed under a stable digest could not.
    """

    def __init__(self, loader: LearnedPredictorLoader, artifacts: ArtifactStore) -> None:
        self.loader = loader
        self.artifacts = artifacts

    def run(self, target_version: RouterModelVersion) -> str:
        """The digest of a successful rehearsal, or :class:`RollbackDrillError`.

        Every failure below is re-raised as one error type on purpose. The caller is a
        promotion route and its decision is binary; the specific exception —
        ``ArtifactNotFoundError``, ``ArtifactDigestMismatchError``,
        ``RouterNotEvaluatedError``, ``FeatureSchemaMismatchError``, ``RankerError`` — is
        preserved in the message and in ``__cause__`` for the operator who has to fix it.
        """

        try:
            # Read the artefact through the store the drill was given, before assembling.
            # ``ArtifactStore.load`` rehashes, so this is the corruption check stated where
            # a reader can see it rather than buried three frames down inside the ranker.
            self.artifacts.load(target_version.artifact_digest)
            predictor = self.loader.assemble(target_version)
            context, candidate, node, objective = _probe()
            row = featurize(
                context, candidate, node, objective, EvidenceSummary(), Vocabulary()
            )
            outcomes, uncertainty = predictor.predict(row.values)
        except Exception as error:
            raise RollbackDrillError(
                target_version.contract_id, f"{type(error).__name__}: {error}"
            ) from error
        return sha256(
            canonical_json(
                {
                    "artifact_digest": target_version.artifact_digest,
                    "calibration_artifact_digest": (
                        target_version.calibration_artifact_digest
                    ),
                    "feature_schema_version": target_version.feature_schema_version,
                    "outcomes": outcomes.model_dump(mode="json"),
                    "uncertainty": uncertainty.model_dump(mode="json"),
                    "version_id": target_version.contract_id,
                }
            )
        ).hexdigest()


class PromotionService:
    """Promote a candidate, or withdraw the promotion, as one transaction each.

    ``clock`` is injected for the reason ``RouterTrainingService`` gives: a version's
    ``created_at`` is inside its ``content_hash``, so two runs of the same promotion are
    only byte-identical when the instant is. ``created_by`` is the service identity that
    mints the version rows; the human who authorised the act arrives per call as
    ``approved_by`` and is what the ledger entry records (OQ-411).

    Every id this service mints is *derived* rather than fresh, so a retried promotion under
    the same idempotency key produces the same documents and the store's immutability guard
    turns the second write into a no-op instead of a conflict. That is what lets the route
    replay a timed-out request without leaving two ``ACTIVE`` rows behind.
    """

    def __init__(
        self,
        store: StateStore,
        ledger: ActivationLedger,
        drill: RollbackDrill,
        *,
        clock: Callable[[], datetime],
        created_by: PrincipalRef,
    ) -> None:
        self.store = store
        self.ledger = ledger
        self.drill = drill
        self.clock = clock
        self.created_by = created_by

    async def promote(
        self,
        report_id: str,
        approved_by: PrincipalRef,
        run_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> RouterActivation:
        """Activate the candidate a ``PROMOTE`` report names, having drilled its reversal.

        The order is the claim (AC4-M8-038): the report is read, its decision is checked,
        its ``rollback_target`` is loaded and scored, and only then does anything get
        written. A refusal at any of those four steps leaves the version table and the
        ledger exactly as they were.

        Replaying it is a no-op that returns the first call's entry. Idempotency is natural
        here rather than keyed: a report authorises **one** release, so a head that already
        names this report *is* the answer to "promote this report", and a retry after a
        timeout cannot leave two ``ACTIVE`` rows behind whatever key it carries.
        ``idempotency_key`` is recorded on the entry for the operator tracing which request
        produced it, and is not what makes the operation safe to repeat.

        Raises ``KeyError`` for an unknown report or candidate — the API's 404 convention,
        which is also what makes another workspace's report invisible rather than forbidden.
        """

        report = await self.store.get_router_promotion_report(report_id)
        if report is None:
            raise KeyError(report_id)
        if report.decision is not RouterPromotionDecision.PROMOTE:
            raise PromotionNotApprovedError(report)

        candidate = await self.store.get_router_model_version(report.candidate_version)
        if candidate is None:
            raise KeyError(report.candidate_version)

        head = await self.ledger.head(
            workspace_id=candidate.workspace_id,
            scope=candidate.scope,
            family_key=family_key_for(candidate),
        )
        if head is not None and head.promotion_report_id == report.contract_id:
            return head

        target = await self.store.get_router_model_version(report.rollback_target)
        if target is None:
            raise RollbackDrillError(
                report.rollback_target,
                "the version is not in the store and cannot be restored",
            )
        drill_digest = self.drill.run(target)

        async with self.store.activation_transaction(candidate.workspace_id) as scoped:
            ledger = self.ledger.bind(scoped)
            # Re-read inside the transaction: the head above was taken before the drill and
            # another promotion may have landed while it ran.
            head = await ledger.head(
                workspace_id=candidate.workspace_id,
                scope=candidate.scope,
                family_key=family_key_for(candidate),
            )
            promoted = self._version_row(
                candidate,
                status=RouterStatus.ACTIVE,
                parent_version_id=candidate.contract_id,
                supersedes_contract_id=candidate.contract_id,
                purpose="promoted",
                cause=report.contract_id,
            )
            retired = await self._tombstone(
                scoped, head, status=RouterStatus.RETIRED, cause=report.contract_id
            )
            activation = await ledger.activate(
                kind=RouterActivationKind.PROMOTE,
                version=promoted,
                previous=retired,
                rollback_target=report.rollback_target,
                promotion_report_id=report.contract_id,
                approved_by=approved_by,
                labels=_labels(drill_digest, idempotency_key),
            )
            await self._announce(
                scoped,
                activation,
                native_type="router.version.promoted",
                normalized_type=EventType.ROUTER_VERSION_PROMOTED,
                run_id=run_id,
                extra={"drill_digest": drill_digest},
            )
        return activation

    async def rollback(
        self,
        version_id: str,
        cause: str,
        approved_by: PrincipalRef,
        run_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> RouterActivation:
        """Withdraw the active version and restore what its activation named.

        ``version_id`` must be the head. A rollback is a statement about the activation the
        operator is looking at, and if something has been promoted since they looked,
        withdrawing the version they named would silently restore a router two promotions
        old.

        The restored target is drilled here too, and not only at promotion time. The drill
        that ran when this version was promoted proved the target loaded *then*; an incident
        an hour or a month later is precisely when that stops being evidence, and a rollback
        onto a target that no longer assembles would replace a bad router with a dead one.

        Replaying it is a no-op that returns the first call's entry, on the same natural key
        promotion uses: a head that is a ``ROLLBACK`` displacing this version *is* the answer
        to "withdraw this version", so a retry during an incident cannot append a second
        withdrawal of something already withdrawn.
        """

        withdrawn = await self.store.get_router_model_version(version_id)
        if withdrawn is None:
            raise KeyError(version_id)
        family_key = family_key_for(withdrawn)
        head = await self.ledger.head(
            workspace_id=withdrawn.workspace_id,
            scope=withdrawn.scope,
            family_key=family_key,
        )
        if (
            head is not None
            and head.kind is RouterActivationKind.ROLLBACK
            and head.previous_version_id == version_id
        ):
            return head
        if head is None or head.router_version_id != version_id:
            raise ActivationConflictError(
                "ROUTER_ACTIVATION_CONFLICT",
                f"router version {version_id} is not the head of "
                f"({withdrawn.workspace_id}, {withdrawn.scope.value}, {family_key}); "
                "only the version now serving can be withdrawn",
            )
        if head.rollback_target_version_id is None:
            raise ActivationConflictError(
                "ROUTER_ACTIVATION_CONFLICT",
                f"activation {head.contract_id} names no rollback target; there is "
                "nothing recorded for this withdrawal to restore",
            )
        target = await self.store.get_router_model_version(
            head.rollback_target_version_id
        )
        if target is None:
            raise RollbackDrillError(
                head.rollback_target_version_id,
                "the version is not in the store and cannot be restored",
            )
        drill_digest = self.drill.run(target)

        async with self.store.activation_transaction(withdrawn.workspace_id) as scoped:
            ledger = self.ledger.bind(scoped)
            # Re-read inside the transaction, exactly as ``promote`` does: the head above was
            # taken before the drill, and a promotion that landed while the drill ran would
            # otherwise be withdrawn under the name of the version the operator was looking at,
            # with the ledger naming one displaced version and the tombstone another.
            head = await ledger.head(
                workspace_id=withdrawn.workspace_id,
                scope=withdrawn.scope,
                family_key=family_key,
            )
            if (
                head is not None
                and head.kind is RouterActivationKind.ROLLBACK
                and head.previous_version_id == version_id
            ):
                return head
            if (
                head is None
                or head.router_version_id != version_id
                or head.rollback_target_version_id != target.contract_id
            ):
                raise ActivationConflictError(
                    "ROUTER_ACTIVATION_CONFLICT",
                    f"router version {version_id} is not the head of "
                    f"({withdrawn.workspace_id}, {withdrawn.scope.value}, {family_key}) "
                    "any more: another activation landed while this rollback was drilled",
                )
            restored = self._version_row(
                target,
                status=RouterStatus.ACTIVE,
                parent_version_id=target.contract_id,
                supersedes_contract_id=target.contract_id,
                purpose="restored",
                cause=cause,
            )
            rolled_back = self._version_row(
                withdrawn,
                status=RouterStatus.ROLLED_BACK,
                parent_version_id=withdrawn.contract_id,
                supersedes_contract_id=withdrawn.contract_id,
                purpose="rolled-back",
                cause=cause,
            )
            activation = await ledger.activate(
                kind=RouterActivationKind.ROLLBACK,
                version=restored,
                previous=rolled_back,
                rollback_target=target.contract_id,
                approved_by=approved_by,
                cause=cause,
                labels=_labels(drill_digest, idempotency_key),
            )
            await self._announce(
                scoped,
                activation,
                native_type="router.version.rolled-back",
                normalized_type=EventType.ROUTER_VERSION_ROLLED_BACK,
                run_id=run_id,
                extra={"cause": cause, "drill_digest": drill_digest},
            )
        return activation

    # ------------------------------------------------------------------ rows

    def _version_row(
        self,
        source: RouterModelVersion,
        *,
        status: RouterStatus,
        parent_version_id: str,
        supersedes_contract_id: str,
        purpose: str,
        cause: str,
    ) -> RouterModelVersion:
        """A new row for an existing artefact, under a new status.

        Every field that describes the *model* — the algorithm, the snapshot it was fitted
        on, both artefact digests, the feature schema — is copied, because none of them has
        changed: promoting and retiring are statements about a version's standing and not
        about its contents. The id is derived from the source, the new status and the cause,
        so a replayed promotion mints the same row and the store's immutability guard makes
        the second write a no-op rather than a conflict.

        Built through ``model_validate`` for the reason ``SnapshotBuilder.build`` gives: the
        pydantic mypy plugin does not carry ``CanonicalContract``'s header fields onto a
        subclass declared in another module, and the blanket ignore a keyword constructor
        would need would also hide a misspelled field.
        """

        labels = dict(source.labels)
        labels[ACTIVATION_PURPOSE_LABEL] = purpose
        return RouterModelVersion.model_validate(
            {
                "contract_id": derived_id(
                    "router_model_version",
                    source.contract_id,
                    status.value,
                    purpose,
                    cause,
                ),
                "created_at": self.clock(),
                "created_by": self.created_by,
                "workspace_id": source.workspace_id,
                "project_id": source.project_id,
                "supersedes_contract_id": supersedes_contract_id,
                "scope": source.scope,
                "algorithm_id": source.algorithm_id,
                "feature_schema_version": source.feature_schema_version,
                "training_snapshot_id": source.training_snapshot_id,
                "artifact_digest": source.artifact_digest,
                "calibration_artifact_digest": source.calibration_artifact_digest,
                "parent_version_id": parent_version_id,
                "status": status,
                "labels": labels,
            }
        )

    async def _tombstone(
        self,
        store: StateStore,
        head: RouterActivation | None,
        *,
        status: RouterStatus,
        cause: str,
    ) -> RouterModelVersion | None:
        """The row recording that the previous head stopped serving, if there was one.

        ``None`` for the first activation of a family, which displaces nothing. Reading the
        displaced version from the store rather than trusting the ledger's copy of its id is
        deliberate: the tombstone has to carry the *artefact* the head was serving, and only
        the version row knows that.
        """

        if head is None:
            return None
        previous = await store.get_router_model_version(head.router_version_id)
        if previous is None:  # pragma: no cover - the ledger cannot name a missing version
            raise ActivationConflictError(
                "ROUTER_ACTIVATION_CONFLICT",
                f"activation {head.contract_id} names router version "
                f"{head.router_version_id}, which is not in the store",
            )
        return self._version_row(
            previous,
            status=status,
            parent_version_id=previous.contract_id,
            supersedes_contract_id=previous.contract_id,
            purpose=status.value.lower().replace("_", "-"),
            cause=cause,
        )

    # ---------------------------------------------------------------- events

    async def _announce(
        self,
        store: StateStore,
        activation: RouterActivation,
        *,
        native_type: str,
        normalized_type: EventType,
        run_id: str | None,
        extra: dict[str, Any],
    ) -> None:
        """Emit §12's promotion event when there is a run to emit it against.

        The same rule ``RouterTrainingService._announce`` documents: the event store is
        run-scoped end to end, ``AgentEvent`` requires a ``run_id``, and a synthesised one
        would invent a run that never executed. A promotion performed from the admin route
        with no run context is recorded durably by the ledger entry itself, which is the
        stronger record anyway.
        """

        if run_id is None:
            return
        run = await store.get_run(run_id)
        if run is None:
            return
        payload: dict[str, Any] = {
            "activation_id": activation.contract_id,
            "family_key": activation.family_key,
            "previous_version_id": activation.previous_version_id,
            "promotion_report_id": activation.promotion_report_id,
            "rollback_target_version_id": activation.rollback_target_version_id,
            "router_version_id": activation.router_version_id,
            "scope": activation.scope.value,
            "sequence": activation.sequence,
        }
        payload.update(extra)
        await store.append_event(
            make_event(
                run_id=run_id,
                session_id=run.session_id or "ses_pending",
                provider=Provider.DETERMINISTIC,
                native_type=native_type,
                normalized_type=normalized_type,
                payload=payload,
                adapter_version=ADAPTER_VERSION,
            )
        )


COHORT_LABEL = "accretion.evaluation-cohort"
"""A record's declared evaluation cohorts, comma separated, on the record's own ``labels``.

Three of OQ-413's five cohorts can be read off an
:class:`~accretion.contracts.routing.ExperienceRecord`'s typed fields and two cannot.
``high_risk`` is the risk class, ``verifier_conflict`` is the contradiction status or a
``VERIFICATION_CONFLICT`` failure, and ``policy`` is a ``POLICY_RISK`` failure or a
``PROHIBITED`` class — all derived, so a workspace gets them without doing anything.
``secrets`` has nothing in the projection to key on: whether a node handled a credential is a
property of what it *did*, and the record is a projection of an outcome. Rather than invent a
proxy that would be wrong in a way nobody could see, that cohort is declared, and a workspace
that declares nothing gets an empty cohort with its zero sample size on the report where a
reader can see it.

Declared and derived membership are unioned rather than one overriding the other: a label is
a claim that this record belongs in a cohort and the derivation is a claim about the same
thing, and neither is entitled to silence the other.
"""

CORRECTNESS_COHORT = "correctness"
POLICY_COHORT = "policy"
SECRETS_COHORT = "secrets"
HIGH_RISK_COHORT = "high_risk"
VERIFIER_CONFLICT_COHORT = "verifier_conflict"

_HIGH_RISK_CLASSES = frozenset(
    {RiskClass.HIGH_DIGITAL, RiskClass.SIMULATION, RiskClass.PHYSICAL_HIGH, RiskClass.PROHIBITED}
)
"""§5.3's classes a regression on is not a tradeoff. ``SIMULATION`` is in the set for the
reason ``_RISK_LEVEL_BY_CLASS`` puts it at ``HIGH``: a simulated physical action can be wrong
in ways a digital one cannot."""

_DECIDED_VERIFICATION = frozenset({VerificationState.PASS, VerificationState.FAIL})
"""The two states in which a verifier reached a judgement. ``INCONCLUSIVE``, ``PENDING`` and
``ERROR`` are the absence of one, and a correctness cohort that counted them would be
measuring how often the verifier ran rather than how often it was satisfied."""


def evaluation_cohorts(record: ExperienceRecord) -> tuple[str, ...]:
    """Every OQ-413 cohort ``record`` belongs to, ascending and unique.

    Membership is many-to-many by design: one record can be high risk *and* a verifier
    conflict, and a partition would force a choice between the two cohorts that a promotion
    gate has no basis for making. Sorted so that two readers of the same record list the same
    cohorts in the same order, which is what makes the report's ``cohort_results`` stable.
    """

    cohorts: set[str] = set()
    declared = record.labels.get(COHORT_LABEL, "")
    cohorts.update(part.strip() for part in declared.split(",") if part.strip())
    if record.local_verification_status in _DECIDED_VERIFICATION:
        cohorts.add(CORRECTNESS_COHORT)
    if record.contract_signature.risk_class in _HIGH_RISK_CLASSES:
        cohorts.add(HIGH_RISK_COHORT)
    if (
        record.contradiction_status is not ContradictionStatus.NONE
        or record.failure_type is FailureType.VERIFICATION_CONFLICT
    ):
        cohorts.add(VERIFIER_CONFLICT_COHORT)
    if (
        record.failure_type is FailureType.POLICY_RISK
        or record.contract_signature.risk_class is RiskClass.PROHIBITED
    ):
        cohorts.add(POLICY_COHORT)
    return tuple(sorted(cohorts))


class HoldoutLeakageError(PromotionError):
    """The holdout is not disjoint from what the candidate was fitted on (AC4-M8-036).

    Raised rather than folded into the report as a rejection, and that asymmetry is
    deliberate. A candidate that regressed produced a *measurement*, and the report is the
    record of it. A candidate measured on projects it was trained on produced no measurement
    at all: every number in the report would be a statement about memorisation wearing the
    name of a generalisation claim, and writing one would leave a document an operator could
    reasonably read and act on. There is nothing here to report, so nothing is written.
    """

    def __init__(self, message: str) -> None:
        super().__init__("ROUTER_HOLDOUT_LEAKAGE", message, status_code=409)


class PromotionReportConflictError(PromotionError):
    """A replayed evaluation found a sealed report of the same id and different contents.

    The store's immutability guard raised, and it is re-raised here as a typed routing error
    for the reason the house convention gives: a client never sees another record's id, and
    an append-only store's ``ValueError`` reaching the HTTP layer would be a 500 for what is
    an ordinary, explainable conflict. It means the evidence moved between two requests
    carrying one ``Idempotency-Key`` — which is exactly when a retry must *not* silently
    return the older verdict.
    """

    def __init__(self, report_id: str, reason: str) -> None:
        super().__init__(
            "ROUTER_PROMOTION_REPORT_CONFLICT",
            f"promotion report {report_id} is already sealed with different contents "
            f"({reason}); the evidence moved between two evaluations sharing one "
            "Idempotency-Key, and a retry may not return a verdict about other rows",
        )


@dataclass(frozen=True, slots=True)
class _HoldoutUnit:
    """One holdout record, reduced to everything the gate reads and nothing else.

    ``cost`` is in ``[-1, 0]`` because :mod:`accretion.routing.ope` is: a verified success
    costs ``-1`` and anything else costs ``0``. ``baseline_cost`` is the *model's* expected
    cost for the same row under the incumbent — the direct-method term the switch estimator
    uses wherever the threshold policy declines to take the logged action, and the only way
    to price a counterfactual nobody logged.
    """

    experience_id: str
    project_id: str
    cost: float
    propensity: float
    candidate_lcb: float
    baseline_cost: float
    cohorts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PolicyValue:
    """The value of one threshold policy, and the diagnostics that say whether to believe it."""

    threshold: float
    value: float
    baseline_value: float
    effective_sample_size: float
    clipped_weight_mass: float
    snips_value: float
    retained: int


class PromotionEvaluator:
    """The CSPI-MT gate (R3) over the Logarithmic-Smoothing estimator (R4), and its report.

    **What it decides.** R3's policy class is the threshold family "take the learned
    configuration only where its lower-confidence success is at least ``c``, and defer to the
    incumbent everywhere else". One policy per threshold on the registered grid, all of them
    scored on the same holdout, the threshold chosen on the *tune* projects and the chosen
    policy tested on the disjoint *evaluation* projects at the 20/80 split
    ``promotion.v1.json`` registers. A candidate is promoted only when the simultaneous lower
    band on its improvement clears ``delta_min`` — an improvement that is *shown*, not one
    that is merely observed.

    **Why the estimator is pessimistic and the band is simultaneous.** Two of the three ways
    a promotion gate lies are here: an unbounded importance weight (answered by
    :func:`~accretion.routing.ope.ls_estimate` with λ = 1/√n) and a threshold chosen after
    seeing the answers (answered by choosing on a disjoint half *and* by
    :func:`~accretion.routing.ope.sup_t_band` over the whole grid). The third — a mean that
    improved while the slice that matters got worse — is answered by ``cohort_results``, and
    by the rule that a failed **critical** cohort rejects whatever the mean did.

    **Every refusal that is a measurement lands in the report; only leakage raises.** A
    regression, an artefact that no longer hashes to its digest, a rollback target that will
    not drill, an uncalibrated candidate and a shadow stage with too little evidence are all
    *findings*, because each of them is something an operator needs written down and
    attributable. Holdout leakage is not a finding, because a report built on a leaking
    holdout would be a document full of numbers that mean nothing (:class:`HoldoutLeakageError`).

    ``vocabulary`` is a constructor argument for the reason
    :class:`~accretion.routing.activation.LedgerActiveVersionResolver`'s ``algorithm_id`` is:
    the snapshot records a vocabulary *digest* and nothing can reconstruct the table from it,
    so the caller that knows which table its workspace freezes passes it, and
    :func:`~accretion.routing.training_snapshot.materialize` refuses a mismatch rather than
    absorbing it.
    """

    def __init__(
        self,
        store: StateStore,
        loader: LearnedPredictorLoader,
        artifacts: ArtifactStore,
        config: PromotionConfig,
        drill: RollbackDrill,
        *,
        vocabulary: Vocabulary | None = None,
    ) -> None:
        self.store = store
        self.loader = loader
        self.artifacts = artifacts
        self.config = config
        self.drill = drill
        self.vocabulary = vocabulary if vocabulary is not None else Vocabulary.frozen_over()

    # ------------------------------------------------------------------ entry

    async def evaluate(
        self,
        candidate_version_id: str,
        baseline_version_id: str,
        holdout_snapshot_id: str,
        principal: PrincipalRef,
        run_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> RouterPromotionReport:
        """Score ``candidate`` against ``baseline`` on ``holdout``, seal the verdict, store it.

        Raises ``KeyError`` for an unknown version or snapshot — the API's 404 convention,
        which is also what keeps another workspace's ids invisible rather than forbidden —
        and :class:`HoldoutLeakageError` when the holdout is not project-disjoint from the
        candidate's training evidence (AC4-M8-036).

        ``idempotency_key`` is what makes a retry a retry. With one, the report's id is
        derived from the four inputs, so a replayed request finds the sealed report and
        returns it; without one, every call is a fresh sealed claim, because two evaluations
        of the same triple a month apart are genuinely two claims about different evidence.

        The ``ROUTER_PROMOTION_EVALUATED`` event is emitted only when ``run_id`` names a
        stored run, on the precedent :meth:`PromotionService._announce` documents: the event
        store is run-scoped end to end and a synthesised run id would invent an execution.
        """

        candidate = await self.store.get_router_model_version(candidate_version_id)
        if candidate is None:
            raise KeyError(candidate_version_id)
        baseline = await self.store.get_router_model_version(baseline_version_id)
        if baseline is None:
            raise KeyError(baseline_version_id)
        if baseline.workspace_id != candidate.workspace_id:
            raise KeyError(baseline_version_id)
        if candidate.contract_id == baseline.contract_id:
            raise HoldoutLeakageError(
                f"router version {candidate.contract_id} is both the candidate and the "
                "baseline; a promotion report compares two versions and this one would "
                "compare a version with itself"
            )
        holdout = await self.store.get_router_training_snapshot(holdout_snapshot_id)
        if holdout is None or holdout.workspace_id != candidate.workspace_id:
            raise KeyError(holdout_snapshot_id)
        training = await self.store.get_router_training_snapshot(
            candidate.training_snapshot_id
        )
        if training is None:
            raise KeyError(candidate.training_snapshot_id)

        report_id = (
            new_id("router_promotion_report")
            if idempotency_key is None
            else derived_id(
                "router_promotion_report",
                candidate.contract_id,
                baseline.contract_id,
                holdout.contract_id,
                idempotency_key,
            )
        )
        if idempotency_key is not None:
            sealed = await self.store.get_router_promotion_report(report_id)
            if sealed is not None:
                return sealed

        await self._refuse_leakage(training, holdout)

        critical: list[RegressionFinding] = []
        tradeoffs: list[RegressionFinding] = []

        try:
            self.drill.run(baseline)
        except RollbackDrillError as error:
            critical.append(
                RegressionFinding(
                    finding_id="ROLLBACK_TARGET_UNDRILLABLE",
                    metric_id="rollback_drill",
                    severity=FindingSeverity.ERROR,
                    description=error.message[:1_000],
                )
            )

        units: list[_HoldoutUnit] = []
        try:
            candidate_predictor = self.loader.assemble(candidate)
            baseline_predictor = self.loader.assemble(baseline)
        except Exception as error:
            critical.append(
                RegressionFinding(
                    finding_id="PREDICTOR_UNASSEMBLABLE",
                    metric_id="artifact_digest",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"{type(error).__name__}: {error}. A version whose stored bytes no "
                        "longer hash to the digest it pinned cannot be scored, and a report "
                        "that scored it anyway would be about different bytes"
                    )[:1_000],
                )
            )
        else:
            units = await self._units(
                workspace_id=candidate.workspace_id,
                holdout=holdout,
                candidate_predictor=candidate_predictor,
                baseline_predictor=baseline_predictor,
            )
            if not units:
                critical.append(
                    RegressionFinding(
                        finding_id="HOLDOUT_UNJOINABLE",
                        metric_id="holdout_rows",
                        severity=FindingSeverity.ERROR,
                        description=(
                            f"snapshot {holdout.contract_id} materialised no scorable row: "
                            "every record either carries no run label or has lost the "
                            "routing decision it was an outcome of, so there is no evidence "
                            "to compare two policies on"
                        ),
                    )
                )

        primary, chosen, cohorts, diagnostics = self._score(units, critical, tradeoffs)
        verified, false_accept, calibration = self._compare_holdouts(
            candidate, baseline, critical
        )
        shadow, shadow_passed = await self._shadow(candidate)
        if not shadow_passed:
            tradeoffs.append(
                RegressionFinding(
                    finding_id="SHADOW_EVIDENCE_INSUFFICIENT",
                    metric_id="shadow_paired_runs",
                    severity=FindingSeverity.WARNING,
                    description=(
                        f"the shadow stage produced {shadow.sample_size} complete paired "
                        f"rollouts with a projected utility delta of "
                        f"{shadow.projected_utility_delta}; §10.2 gates promotion on shadow "
                        "evidence and this candidate has not cleared it"
                    ),
                    disclosed_bound=(
                        f"min_paired_runs={self.config.shadow_min_paired_runs}, "
                        f"delta_ni={self.config.delta_ni}"
                    ),
                )
            )

        blocked = bool(critical) or any(
            cohort.critical and not cohort.comparison.passed for cohort in cohorts
        )
        if not verified.passed or not false_accept.passed:
            blocked = True
        if blocked:
            decision = RouterPromotionDecision.REJECT
        elif primary.passed and shadow_passed:
            decision = RouterPromotionDecision.PROMOTE
        else:
            decision = RouterPromotionDecision.REQUIRE_REVIEW

        report = RouterPromotionReport.model_validate(
            {
                "contract_id": report_id,
                "created_by": principal,
                "workspace_id": candidate.workspace_id,
                "candidate_version": candidate.contract_id,
                "baseline_version": baseline.contract_id,
                "training_snapshot_id": candidate.training_snapshot_id,
                "holdout_definition_id": holdout.contract_id,
                "primary_metric_result": primary,
                "verified_success_non_regression": verified,
                "false_acceptance_non_regression": false_accept,
                "calibration_result": calibration,
                "cohort_results": cohorts,
                "shadow_result": shadow,
                "critical_regressions": critical,
                "noncritical_tradeoffs": tradeoffs,
                "rollback_target": baseline.contract_id,
                "decision": decision,
                "approved_by": (
                    principal if decision is RouterPromotionDecision.PROMOTE else None
                ),
                "labels": {
                    "accretion.acceptance-threshold": f"{chosen}",
                    "accretion.promotion-config": self.config.config_version,
                    **diagnostics,
                    **({IDEMPOTENCY_LABEL: idempotency_key} if idempotency_key else {}),
                },
            }
        )
        try:
            stored = await self.store.put_router_promotion_report(report)
        except ValueError as error:
            raise PromotionReportConflictError(report_id, str(error)) from error
        await self._announce_evaluation(stored, run_id=run_id)
        return stored

    # -------------------------------------------------------------- leakage

    async def _refuse_leakage(
        self, training: RouterTrainingSnapshot, holdout: RouterTrainingSnapshot
    ) -> None:
        """Both halves of AC4-M8-036: the declared split, and the records actually named.

        :func:`~accretion.routing.split.assert_disjoint` checks what the two snapshots
        *declare* — the cheap half, and the half a snapshot can satisfy while still leaking,
        because ``included_experience_ids`` is the manifest and ``split`` is a description of
        it. So the second check reads both manifests, dereferences every record to the
        project it came from, and refuses any project on both sides. A candidate scored on a
        project it was fitted on is measuring memorisation, and there is no threshold, band
        or cohort that repairs that.
        """

        declared = SplitAssignment(
            seed=self.config.seed,
            fractions=DEFAULT_FRACTIONS,
            root_by_project={
                project_id: project_id
                for project_id in (
                    *training.split.training_project_ids,
                    *training.split.validation_project_ids,
                    *holdout.split.holdout_project_ids,
                )
            },
            train_project_ids=sorted(set(training.split.training_project_ids)),
            development_project_ids=sorted(
                set(training.split.validation_project_ids)
                - set(training.split.training_project_ids)
            ),
            test_project_ids=sorted(set(holdout.split.holdout_project_ids)),
        )
        try:
            assert_disjoint(declared)
        except SplitViolation as error:
            raise HoldoutLeakageError(
                f"the declared split of training snapshot {training.contract_id} and "
                f"holdout snapshot {holdout.contract_id} leaks: {error}"
            ) from error

        training_projects = set(training.split.training_project_ids)
        training_projects |= await self._manifest_projects(training)
        holdout_projects = await self._manifest_projects(holdout)
        shared = sorted(training_projects & holdout_projects)
        if shared:
            raise HoldoutLeakageError(
                f"holdout snapshot {holdout.contract_id} names records from projects "
                f"{shared!r}, which the candidate was fitted on; §10.1 splits by project "
                "because two nodes from one project share an objective, a repository and a "
                "policy set, and a holdout that leaks measures memorisation"
            )

    async def _manifest_projects(self, snapshot: RouterTrainingSnapshot) -> set[str]:
        """The projects the records a snapshot *names* actually came from.

        Read through the store rather than off ``split``, because ``split`` is what the
        snapshot claims and the manifest is what it contains. A record that is no longer in
        the store contributes no project rather than raising: whether the snapshot can still
        be rebuilt is :func:`~accretion.routing.training_snapshot.materialize`'s question and
        it is asked, with its own error, a few lines later.
        """

        projects: set[str] = set()
        for experience_id in snapshot.included_experience_ids:
            record = await self.store.get_experience_record(experience_id)
            if record is not None and record.project_id is not None:
                projects.add(record.project_id)
        return projects

    # ---------------------------------------------------------------- units

    async def _units(
        self,
        *,
        workspace_id: str,
        holdout: RouterTrainingSnapshot,
        candidate_predictor: LearnedOutcomePredictor,
        baseline_predictor: LearnedOutcomePredictor,
    ) -> list[_HoldoutUnit]:
        """Rebuild the holdout and reduce each row to a scorable unit.

        The join is the one M4's trainer performs and it fails in the same places: a record
        with no run label is not evidence about a run's outcome, and a record whose routing
        decision, configuration candidate or objective is no longer stored cannot be
        featurized at all. Both are skipped rather than raised on — the caller counts what
        survived and refuses an empty holdout with a named finding, which says more than an
        exception thrown from four frames down.

        ``propensity`` comes from the receipt that logged the decision, and is
        :data:`~accretion.routing.stages.DETERMINISTIC_PROPENSITY` where no receipt recorded
        one. That is a measurement and not a default: a deterministic router had no choice,
        so the probability it took the action it took is exactly one, and §9.5 records the
        propensity precisely so this estimator does not have to guess.
        """

        table = await materialize(holdout, self.store, self.vocabulary)
        records = {
            record.contract_id: record
            for record in await self.store.list_experience_records(workspace_id=workspace_id)
        }
        nodes = {
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
        propensities: dict[tuple[str, str], float] = {}
        for receipt in await self.store.list_routing_receipts(workspace_id=workspace_id):
            if receipt.selected_configuration_hash is None:
                continue
            if receipt.selection_propensity is None:
                continue
            propensities.setdefault(
                (receipt.routing_request_id, receipt.selected_configuration_hash),
                receipt.selection_propensity,
            )
        objectives: dict[str, ObjectiveContract] = {}
        by_project: dict[str, list[ExperienceRecord]] = {}
        for known in records.values():
            if known.project_id is not None:
                by_project.setdefault(known.project_id, []).append(known)

        units: list[_HoldoutUnit] = []
        for row, label in zip(table.rows, table.labels_final, strict=True):
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
                found = await self.store.get_objective_contract(
                    reference.objective_contract_id
                )
                if found is None:
                    continue
                objectives[reference.objective_contract_id] = found
                objective = found
            evidence = summarize_evidence(
                [
                    sibling
                    for sibling in by_project.get(record.project_id or "", [])
                    if sibling.created_at < record.created_at
                    and sibling.contract_id != record.contract_id
                ],
                signature=record.contract_signature,
                configuration_hash=record.configuration_hash,
                as_of=record.created_at,
            )
            features = featurize(
                context, configuration, node, objective, evidence, self.vocabulary
            )
            candidate_outcome, _ = candidate_predictor.predict(features.values)
            baseline_outcome, _ = baseline_predictor.predict(features.values)
            propensity = propensities.get(
                (context.contract_id, row.configuration_hash), DETERMINISTIC_PROPENSITY
            )
            units.append(
                _HoldoutUnit(
                    experience_id=row.experience_id,
                    project_id=row.project_id,
                    cost=-1.0 if float(label) >= 1.0 else 0.0,
                    propensity=max(propensity, _MINIMUM_PROPENSITY),
                    candidate_lcb=candidate_outcome.run_verified_success.lower_bound,
                    baseline_cost=-baseline_outcome.run_verified_success.mean,
                    cohorts=evaluation_cohorts(record),
                )
            )
        return units

    # ---------------------------------------------------------------- score

    def _policy_value(self, units: Sequence[_HoldoutUnit], threshold: float) -> _PolicyValue:
        """The switch estimate of one threshold policy's risk, with its diagnostics.

        Where the policy takes the logged action the term is R4's smoothed importance
        weighting; where it defers to the incumbent the term is the incumbent model's own
        expected cost. Deferring is expressed as a *zero weight* rather than as a shorter
        list, so ``n`` — and therefore λ = 1/√n — is the size of the holdout and not the size
        of the subset this threshold happened to accept. A λ that grew as the threshold got
        stricter would make the most selective policy look best for a reason that has nothing
        to do with the policy.
        """

        weights = [
            (1.0 / unit.propensity) if unit.candidate_lcb >= threshold else 0.0
            for unit in units
        ]
        costs = [unit.cost for unit in units]
        deferred = math.fsum(
            unit.baseline_cost for unit, weight in zip(units, weights, strict=True) if weight == 0.0
        ) / float(len(units))
        lam = lambda_rule(len(units))
        return _PolicyValue(
            threshold=threshold,
            value=ls_estimate(weights, costs, lam) + deferred,
            baseline_value=math.fsum(unit.baseline_cost for unit in units) / float(len(units)),
            effective_sample_size=ess(weights),
            clipped_weight_mass=clip_mass(weights, _CLIP_DIAGNOSTIC_TAU),
            snips_value=snips(weights, costs),
            retained=sum(1 for weight in weights if weight > 0.0),
        )

    @staticmethod
    def _contribution(unit: _HoldoutUnit, threshold: float, lam: float) -> float:
        """One unit's signed contribution to the *improvement* at ``threshold``.

        ``baseline_cost - own_cost`` under the same switch rule the estimate uses, so the
        mean of these over a resample is the improvement that resample would report and the
        bootstrap is over the paired contrast rather than over two arms subtracted after the
        fact. Positive means this record is one the candidate handled better.
        """

        if unit.candidate_lcb >= threshold:
            weight = 1.0 / unit.propensity
            own = -math.log1p(-lam * weight * unit.cost) / lam
        else:
            own = unit.baseline_cost
        return unit.baseline_cost - own

    def _score(
        self,
        units: Sequence[_HoldoutUnit],
        critical: list[RegressionFinding],
        tradeoffs: list[RegressionFinding],
    ) -> tuple[MetricComparison, float, list[CohortResult], dict[str, str]]:
        """Choose ``c*`` on the tune projects and test it on the evaluation projects.

        Returns the primary comparison, the threshold it was read at, one
        :class:`~accretion.contracts.routing.CohortResult` per cohort present in the holdout
        or named critical by the registered config — the second half of that condition is
        what makes a critical cohort with no members visible as a zero rather than absent —
        and the labels carrying the estimator's own diagnostics.

        ``ESS``, the clip mass and the SNIPS cross-check go on the report as labels rather
        than into a finding, because they are not verdicts: they are how a reader decides
        whether to believe the verdict. "n = 400" and "ESS = 6" are the same holdout and
        entirely different evidence, and a report that recorded only the first would let the
        second be discovered by someone re-deriving it.
        """

        grid = self.config.thresholds()
        if not units:
            return (_no_comparison("policy_value_improvement"), grid[0], [], {})

        project_ids = sorted({unit.project_id for unit in units})
        tune_projects, eval_projects = self.config.tune_projects(project_ids)
        tune = [unit for unit in units if unit.project_id in set(tune_projects)]
        evaluation = [unit for unit in units if unit.project_id in set(eval_projects)]
        if not tune:
            tradeoffs.append(
                RegressionFinding(
                    finding_id="NO_TUNE_SPLIT",
                    metric_id="policy_value_improvement",
                    severity=FindingSeverity.INFO,
                    description=(
                        f"the holdout spans {len(project_ids)} project(s), too few to split "
                        "into a tune half and an evaluation half; the acceptance threshold "
                        "was fixed at the most permissive grid point rather than chosen"
                    ),
                    disclosed_bound=f"threshold={grid[0]}",
                )
            )
            chosen = grid[0]
        else:
            chosen = max(
                grid,
                key=lambda threshold: (
                    self._policy_value(tune, threshold).baseline_value
                    - self._policy_value(tune, threshold).value,
                    -threshold,
                ),
            )

        band = self._band(evaluation, grid)
        index = grid.index(chosen)
        scored = self._policy_value(evaluation, chosen)
        delta = scored.baseline_value - scored.value
        lower, upper = band[index]
        lower = min(lower, delta)
        upper = max(upper, delta)
        primary = MetricComparison(
            metric_id="policy_value_improvement",
            baseline_value=-scored.baseline_value,
            candidate_value=-scored.value,
            delta=delta,
            delta_lower_bound=lower,
            delta_upper_bound=upper,
            passed=lower >= self.config.delta_min,
        )
        if not primary.passed and lower < self.config.delta_ni:
            critical.append(
                RegressionFinding(
                    finding_id="POLICY_VALUE_REGRESSION",
                    metric_id="policy_value_improvement",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"the simultaneous lower band on the improvement at threshold "
                        f"{chosen} is {lower}, below the non-inferiority margin "
                        f"{self.config.delta_ni}; ESS {scored.effective_sample_size} over "
                        f"{scored.retained} retained rows, SNIPS {scored.snips_value}"
                    )[:1_000],
                )
            )
        diagnostics = {
            "accretion.clipped-weight-mass": repr(scored.clipped_weight_mass),
            "accretion.effective-sample-size": repr(scored.effective_sample_size),
            "accretion.retained-rows": str(scored.retained),
            "accretion.scored-rows": str(len(evaluation)),
            "accretion.snips-diagnostic": repr(scored.snips_value),
        }
        return (primary, chosen, self._cohorts(units, chosen), diagnostics)

    def _band(self, units: Sequence[_HoldoutUnit], grid: Sequence[float]) -> list[Interval]:
        """The sup-t simultaneous band over the whole grid, clustered by project.

        Every unit contributes one vector — its improvement at each threshold — so a
        replicate scores the whole grid on one resample and the band takes the thresholds'
        correlation from the data. Bonferroni over ten nearly-identical policies would be
        enormously conservative; the registered ``multiplicity`` is what governs the *family*
        of comparisons the report makes, and this is the within-family band.
        """

        if not units:
            return [(0.0, 0.0) for _ in grid]
        lam = lambda_rule(len(units))
        groups: dict[str, list[list[float]]] = {}
        for unit in units:
            groups.setdefault(unit.project_id, []).append(
                [self._contribution(unit, threshold, lam) for threshold in grid]
            )
        return sup_t_band(
            groups,
            self.config.bootstrap_replicates,
            self.config.seed,
            self.config.alpha,
        )

    def _cohorts(
        self, units: Sequence[_HoldoutUnit], threshold: float
    ) -> list[CohortResult]:
        """One comparison per cohort, at the chosen threshold, at the corrected level.

        The per-cohort level is ``bonferroni(alpha, k)`` over the cohorts examined, because
        the cohorts are a *family* — the gate looks at all of them and blocks on the worst,
        which is exactly the situation an uncorrected level is wrong for. Within a cohort the
        interval is the project-clustered percentile bootstrap, the same estimator the shadow
        report quotes, so two documents about the same workspace do not disagree about what a
        cluster is.

        A cohort with no members passes with a zero delta and a recorded ``sample_size`` of
        zero. That is the only honest reading: an absent cohort produced no evidence of a
        regression, and failing it would block every promotion in a workspace that has never
        seen a secret-handling node.
        """

        present = {cohort for unit in units for cohort in unit.cohorts}
        examined = sorted(present | set(self.config.critical_cohorts))
        if not examined:
            return []
        level = bonferroni(self.config.alpha, len(examined))
        lam = lambda_rule(len(units)) if units else 1.0
        results: list[CohortResult] = []
        for cohort_id in examined:
            members = [unit for unit in units if cohort_id in unit.cohorts]
            critical = cohort_id in self.config.critical_cohorts
            if not members:
                results.append(
                    CohortResult(
                        cohort_id=cohort_id,
                        description=_COHORT_DESCRIPTIONS.get(
                            cohort_id, "a registered evaluation cohort"
                        )
                        + "; no holdout record belongs to it",
                        sample_size=0,
                        critical=critical,
                        comparison=_no_comparison(cohort_id, passed=True),
                    )
                )
                continue
            groups: dict[str, list[float]] = {}
            for unit in members:
                groups.setdefault(unit.project_id, []).append(
                    self._contribution(unit, threshold, lam)
                )
            lower, upper = hierarchical_bootstrap(
                groups,
                _mean_of,
                self.config.bootstrap_replicates,
                self.config.seed,
                level,
            )
            baseline_value = math.fsum(unit.baseline_cost for unit in members) / len(members)
            delta = math.fsum(
                self._contribution(unit, threshold, lam) for unit in members
            ) / len(members)
            results.append(
                CohortResult(
                    cohort_id=cohort_id,
                    description=_COHORT_DESCRIPTIONS.get(
                        cohort_id, "a registered evaluation cohort"
                    ),
                    sample_size=len(members),
                    critical=critical,
                    comparison=MetricComparison(
                        metric_id=cohort_id,
                        baseline_value=-baseline_value,
                        candidate_value=-(baseline_value - delta),
                        delta=delta,
                        delta_lower_bound=min(lower, delta),
                        delta_upper_bound=max(upper, delta),
                        passed=min(lower, delta) >= self.config.delta_ni,
                    ),
                )
            )
        return results

    # ------------------------------------------------------- holdout documents

    def _compare_holdouts(
        self,
        candidate: RouterModelVersion,
        baseline: RouterModelVersion,
        critical: list[RegressionFinding],
    ) -> tuple[MetricComparison, MetricComparison, MetricComparison]:
        """§10.2's three mandatory gates, read off the two sealed holdout evaluations.

        These are *not* recomputed from the rows above, and that is the point: each version's
        :class:`~accretion.routing.train.HoldoutEvaluation` is the document the loader already
        refuses to route without, sealed at fitting time and digest-pinned by the version. A
        promotion gate that recomputed them would be grading its own homework with a second
        pencil; reading the sealed claims makes the comparison falsifiable by anyone holding
        the two digests.

        The intervals on the two rate comparisons are conservative Clopper–Pearson
        differences — the candidate's lower limit against the baseline's upper — because the
        two evaluations are independent samples and nothing joins them row by row. The
        calibration comparison carries a degenerate interval: ECE has no closed form here and
        its gate is an absolute ceiling rather than a contrast, so an invented width would be
        the only fiction in the report.
        """

        candidate_holdout = self._read_holdout(candidate, critical)
        baseline_holdout = self._read_holdout(baseline, critical)
        if candidate_holdout is None or baseline_holdout is None:
            return (
                _no_comparison("verified_success_rate"),
                _no_comparison("false_acceptance_rate"),
                _no_comparison("ece_10bin"),
            )

        verified_delta, verified_bounds = _rate_difference(
            candidate_holdout.observed_verified_success_rate,
            candidate_holdout.n_rows,
            baseline_holdout.observed_verified_success_rate,
            baseline_holdout.n_rows,
            self.config.alpha,
        )
        lcb_held = (
            candidate_holdout.verified_success_lcb
            >= baseline_holdout.verified_success_lcb + self.config.delta_ni
        )
        verified = MetricComparison(
            metric_id="verified_success_rate",
            baseline_value=baseline_holdout.observed_verified_success_rate,
            candidate_value=candidate_holdout.observed_verified_success_rate,
            delta=verified_delta,
            delta_lower_bound=verified_bounds[0],
            delta_upper_bound=verified_bounds[1],
            passed=verified_bounds[0] >= self.config.delta_ni and lcb_held,
        )
        if not verified.passed:
            critical.append(
                RegressionFinding(
                    finding_id="VERIFIED_SUCCESS_REGRESSION",
                    metric_id="verified_success_rate",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"verified success fell from {baseline_holdout.verified_success_lcb} "
                        f"to {candidate_holdout.verified_success_lcb} on the calibrated lower "
                        f"bound, with a rate difference bounded below by {verified_bounds[0]} "
                        f"against a margin of {self.config.delta_ni}"
                    )[:1_000],
                )
            )

        false_delta, false_bounds = _rate_difference(
            candidate_holdout.false_acceptance_rate,
            candidate_holdout.n_rows,
            baseline_holdout.false_acceptance_rate,
            baseline_holdout.n_rows,
            self.config.alpha,
        )
        false_accept = MetricComparison(
            metric_id="false_acceptance_rate",
            baseline_value=baseline_holdout.false_acceptance_rate,
            candidate_value=candidate_holdout.false_acceptance_rate,
            delta=false_delta,
            delta_lower_bound=false_bounds[0],
            delta_upper_bound=false_bounds[1],
            passed=false_bounds[1] <= -self.config.delta_ni,
        )
        if not false_accept.passed:
            critical.append(
                RegressionFinding(
                    finding_id="FALSE_ACCEPTANCE_REGRESSION",
                    metric_id="false_acceptance_rate",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"false acceptance rose from {baseline_holdout.false_acceptance_rate} "
                        f"to {candidate_holdout.false_acceptance_rate}, an increase bounded "
                        f"above by {false_bounds[1]} against a ceiling of "
                        f"{-self.config.delta_ni}"
                    )[:1_000],
                )
            )

        calibration_delta = candidate_holdout.ece_10bin - baseline_holdout.ece_10bin
        calibration = MetricComparison(
            metric_id="ece_10bin",
            baseline_value=baseline_holdout.ece_10bin,
            candidate_value=candidate_holdout.ece_10bin,
            delta=calibration_delta,
            delta_lower_bound=calibration_delta,
            delta_upper_bound=calibration_delta,
            passed=candidate_holdout.ece_10bin <= self.config.calibration_max_ece,
        )
        if not calibration.passed:
            critical.append(
                RegressionFinding(
                    finding_id="CALIBRATION_CEILING_EXCEEDED",
                    metric_id="ece_10bin",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"the candidate's 10-bin expected calibration error is "
                        f"{candidate_holdout.ece_10bin}, above the registered ceiling "
                        f"{self.config.calibration_max_ece}; every lower bound this router "
                        "produces is read as a guarantee and an uncalibrated one is not"
                    ),
                )
            )
        return (verified, false_accept, calibration)

    def _read_holdout(
        self, version: RouterModelVersion, critical: list[RegressionFinding]
    ) -> HoldoutEvaluation | None:
        """The sealed holdout evaluation a version pinned, or a named critical finding."""

        digest = version.labels.get(ACCEPTANCE_LABEL)
        if not digest:
            critical.append(
                RegressionFinding(
                    finding_id="HOLDOUT_EVALUATION_MISSING",
                    metric_id="holdout_eval_digest",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"router version {version.contract_id} carries no "
                        f"{ACCEPTANCE_LABEL}: it was never evaluated on a project-disjoint "
                        "holdout, and §10.1-10.2 put offline ranking before any promotion"
                    ),
                )
            )
            return None
        try:
            return HoldoutEvaluation.model_validate(json.loads(self.artifacts.load(digest)))
        except Exception as error:
            critical.append(
                RegressionFinding(
                    finding_id="HOLDOUT_EVALUATION_UNREADABLE",
                    metric_id="holdout_eval_digest",
                    severity=FindingSeverity.ERROR,
                    description=(
                        f"the holdout evaluation {digest} pinned by router version "
                        f"{version.contract_id} could not be read back: "
                        f"{type(error).__name__}: {error}"
                    )[:1_000],
                )
            )
            return None

    # --------------------------------------------------------------- shadow

    async def _shadow(self, candidate: RouterModelVersion) -> tuple[ShadowSummary, bool]:
        """M6.1's shadow report for the SHADOW version parented on this candidate.

        The gate is applied here rather than inherited from whatever the report was rendered
        under, which is what :func:`~accretion.routing.shadow.shadow_gate`'s threshold
        arguments exist for: a promotion asks for the bar ``promotion.v1.json`` registers,
        and a dashboard that had asked for less does not lower it.

        A candidate with no shadow stage at all summarises to zeros and does not pass. An
        empty sample is not a null result — a report saying "agreement 0.0 over 0 decisions,
        non-inferior" would be an assertion made from nothing — so the count is recorded, the
        gate fails, and the caller turns that into a disclosed tradeoff and a review rather
        than a rejection.
        """

        shadow_versions = [
            version
            for version in await self.store.list_router_model_versions(
                workspace_id=candidate.workspace_id
            )
            if version.status is RouterStatus.SHADOW
            and version.parent_version_id == candidate.contract_id
        ]
        empty = ShadowSummary(
            decision_count=0, agreement_rate=0.0, projected_utility_delta=0.0, sample_size=0
        )
        if not shadow_versions:
            return (empty, False)
        shadow_ids = {version.contract_id for version in shadow_versions}
        decisions = [
            decision
            for decision in await self.store.list_shadow_decisions(
                workspace_id=candidate.workspace_id
            )
            if decision.shadow_router_version_id in shadow_ids
        ]
        if not decisions:
            return (empty, False)
        newest = max(shadow_ids)
        decisions = [
            decision
            for decision in decisions
            if decision.shadow_router_version_id == newest
        ]
        results = await self.store.list_shadow_rollout_results(
            workspace_id=candidate.workspace_id
        )
        report = shadow_report(
            results,
            decisions,
            weights=self.config.utility_weights,
            config=ShadowReportConfig(
                seed=self.config.seed,
                bootstraps=self.config.bootstrap_replicates,
                alpha=self.config.alpha,
                min_paired_runs=self.config.shadow_min_paired_runs,
                delta_ni=self.config.delta_ni,
            ),
        )
        summary = ShadowSummary(
            decision_count=len(decisions),
            agreement_rate=report.agreement_rate,
            projected_utility_delta=report.mean_delta,
            sample_size=report.paired_count,
        )
        passed = shadow_gate(
            report,
            min_paired_runs=self.config.shadow_min_paired_runs,
            delta_ni=self.config.delta_ni,
        )
        return (summary, passed)

    # ---------------------------------------------------------------- events

    async def _announce_evaluation(
        self, report: RouterPromotionReport, *, run_id: str | None
    ) -> None:
        """§12's ``ROUTER_PROMOTION_EVALUATED``, when there is a run to emit it against.

        The rule :meth:`PromotionService._announce` states, restated because the reason is
        the same and the consequence here is larger: an evaluation performed from the admin
        route has no run context, and a synthesised ``run_id`` would attach the most
        consequential event in this milestone to an execution that never happened. The report
        itself is the durable record; the event is the run-scoped projection of it
        (ADR4-M8-004).
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
                native_type="router.promotion.evaluated",
                normalized_type=EventType.ROUTER_PROMOTION_EVALUATED,
                payload={
                    "baseline_version": report.baseline_version,
                    "candidate_version": report.candidate_version,
                    "critical_regression_count": len(report.critical_regressions),
                    "decision": report.decision.value,
                    "holdout_definition_id": report.holdout_definition_id,
                    "primary_delta": report.primary_metric_result.delta,
                    "primary_delta_lower_bound": (
                        report.primary_metric_result.delta_lower_bound
                    ),
                    "report_id": report.contract_id,
                    "rollback_target": report.rollback_target,
                },
                adapter_version=ADAPTER_VERSION,
            )
        )


_MINIMUM_PROPENSITY = 1e-6
"""The floor a logged propensity is read at before it becomes a denominator.

Not clipping of the *weight* — that is reported by :func:`~accretion.routing.ope.clip_mass`
and never applied — but a guard against a division by zero. A propensity of exactly zero
says the behaviour policy could not have taken the action it is recorded as having taken,
which is a corrupt log rather than an extreme weight, and this floor turns it into a very
large weight the pessimistic estimator then refuses to be impressed by.
"""

_CLIP_DIAGNOSTIC_TAU = 10.0
"""The threshold the reported clip mass is measured at.

Ten is "one order of magnitude above a uniform weight". It decides nothing — the estimate
is unclipped — and it exists so the diagnostic is comparable between two reports, which it
would not be if each one chose its own.
"""

_COHORT_DESCRIPTIONS = {
    CORRECTNESS_COHORT: "nodes whose own verifier reached a PASS or FAIL judgement",
    HIGH_RISK_COHORT: "nodes at HIGH_DIGITAL, SIMULATION, PHYSICAL_HIGH or PROHIBITED risk",
    POLICY_COHORT: "nodes that failed on policy risk or act at a prohibited risk class",
    SECRETS_COHORT: "nodes declared as handling credentials or secret material",
    VERIFIER_CONFLICT_COHORT: (
        "nodes under an open or resolved contradiction, or a verifier conflict"
    ),
}
"""What each OQ-413 cohort id means, written where the report's readers are."""


def _mean_of(values: Sequence[float]) -> float:
    """The arithmetic mean, as a named statistic the bootstrap can be handed."""

    return math.fsum(values) / float(len(values))


def _no_comparison(metric_id: str, *, passed: bool = False) -> MetricComparison:
    """A comparison that measured nothing, stated as such rather than as a zero.

    Every field is zero and ``passed`` says whether the absence of evidence is allowed to
    pass. It is ``False`` for a gate that could not be computed — a candidate whose artefact
    would not load has not shown non-regression — and ``True`` only for a cohort with no
    members, where there is genuinely no regression to have found.
    """

    return MetricComparison(
        metric_id=metric_id,
        baseline_value=0.0,
        candidate_value=0.0,
        delta=0.0,
        delta_lower_bound=0.0,
        delta_upper_bound=0.0,
        passed=passed,
    )


def _rate_difference(
    candidate_rate: float,
    candidate_n: int,
    baseline_rate: float,
    baseline_n: int,
    alpha: float,
) -> tuple[float, Interval]:
    """The difference of two rates, with a conservative Clopper-Pearson interval on it.

    ``(candidate_lo - baseline_hi, candidate_hi - baseline_lo)``: the widest difference the
    two independent exact intervals admit. Conservative on purpose — the two holdout
    evaluations are separate samples with no row-level pairing, so anything tighter would be
    claiming a correlation nobody measured — and it always brackets the observed difference,
    because each rate lies inside its own interval.
    """

    candidate_low, candidate_high = clopper_pearson(
        round(candidate_rate * candidate_n), candidate_n, alpha
    )
    baseline_low, baseline_high = clopper_pearson(
        round(baseline_rate * baseline_n), baseline_n, alpha
    )
    delta = candidate_rate - baseline_rate
    lower = min(candidate_low - baseline_high, delta)
    upper = max(candidate_high - baseline_low, delta)
    return (delta, (lower, upper))


def build_promotion_evaluator(
    store: StateStore,
    drill: RollbackDrill,
    *,
    config: PromotionConfig | None = None,
    vocabulary: Vocabulary | None = None,
) -> PromotionEvaluator:
    """Assemble the gate from a deployment's promotion service and the registered constants.

    Takes the *drill* rather than an artefact root because the drill already holds both
    collaborators the evaluator needs — the loader and the artefact store — and because the
    two must be the same objects: an evaluator that rehearsed the rollback target through one
    artefact store and scored the candidate through another would be making two claims about
    two deployments. ``config`` defaults to the registered
    ``evals/router/promotion.v1.json``, parsed once per process.
    """

    return PromotionEvaluator(
        store,
        drill.loader,
        drill.artifacts,
        config if config is not None else default_promotion_config(),
        drill,
        vocabulary=vocabulary,
    )


def build_promotion_service(
    store: StateStore,
    artifacts: ArtifactStore,
    *,
    operator_identity: str,
    clock: Callable[[], datetime] | None = None,
) -> PromotionService:
    """Assemble the service and its three collaborators from what a deployment has.

    A factory rather than four lines in ``api/main.py``'s lifespan, for the reason
    ``build_node_routing`` is a factory: the wiring — which loader the drill uses, which
    store the ledger reads, that the drill and the loader share one artefact root — is a
    property of this subsystem and not of the process that starts it. ``mcp_gateway.py``
    and any future entry point get the same object without restating it.

    ``operator_identity`` becomes the ``created_by`` on the version rows the service mints:
    the rows are made by the deployment, and the human who authorised the release arrives
    per call as ``approved_by`` and is what the ledger entry records.
    """

    loader = LearnedPredictorLoader(store, artifacts)
    return PromotionService(
        store,
        ActivationLedger(store),
        RollbackDrill(loader, artifacts),
        clock=clock if clock is not None else (lambda: datetime.now(UTC)),
        created_by=PrincipalRef(
            principal_id=operator_identity,
            display_name="Accretion promotion service",
            status=PrincipalStatus.ACTIVE,
        ),
    )


def _labels(drill_digest: str, idempotency_key: str | None) -> dict[str, str]:
    """The ledger entry's labels: what the drill produced, and which request asked.

    The key is recorded and is deliberately *not* what makes the operation repeatable —
    promotion and rollback are idempotent on their own natural keys, so a client that
    retried with a fresh key still gets the first entry rather than a second one.
    """

    labels = {DRILL_DIGEST_LABEL: drill_digest}
    if idempotency_key is not None:
        labels[IDEMPOTENCY_LABEL] = idempotency_key
    return labels


ACTIVATION_PURPOSE_LABEL = "accretion.activation-purpose"
"""Why this copy of an artefact exists: ``promoted``, ``restored``, ``retired``, ``rolled-back``.

Part of the derived id's input as well as a label, so that promoting and then rolling back
and then promoting the same candidate again mints three distinguishable rows rather than
colliding on one.
"""


@cache
def _probe() -> tuple[RoutingContext, ExecutionConfiguration, NodeContract, ObjectiveContract]:
    """The four sealed documents the drill featurizes, validated once per process.

    Cached rather than built at import: a contract change that broke these would otherwise
    take the whole ``accretion.routing`` package down at import time with a traceback that
    named a pydantic validator instead of the drill, and a failure that can only be seen by
    running the thing that failed is easier to diagnose than one that stops everything.
    """

    documents: dict[str, Any] = json.loads(_PROBE_DOCUMENTS)
    return (
        RoutingContext.model_validate(documents["routing_context"]),
        ExecutionConfiguration.model_validate(documents["execution_configuration"]),
        NodeContract.model_validate(documents["node_contract"]),
        ObjectiveContract.model_validate(documents["objective_contract"]),
    )


_PROBE_DOCUMENTS = r"""
{
 "execution_configuration": {
  "content_hash": "7ad257a67d482056803865e397ef15da378968607e250e2706eab9f9ce6108ef",
  "contract_id": "cfg_WBBEG19RNW9T66A2ZFGGB0C2D9",
  "created_at": "2026-03-01T09:00:00Z",
  "created_by": {
   "display_name": "v0.4 contract freeze fixture",
   "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
   "status": "ACTIVE"
  },
  "environment": {
   "environment": {
    "environment_id": "sandboxed-worktree",
    "image_digest": "287e34b12a9a744772571abf042fa288e44a38799e5792362e3754de304490fb",
    "policy_profile": "restricted-egress"
   },
   "workspace_isolation": "worktree"
  },
  "model": {
   "inference_profile": {
    "stream": true,
    "temperature": 0.2,
    "thinking_budget": 8000
   },
   "model_id": "claude-opus-4",
   "provider": "CLAUDE"
  },
  "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
  "runtime": {
   "adapter_version": "accretion-claude-v1",
   "capability_profile_digest": "d5c06c282061049bff56775278552ec601647511e1bdb6547323d345e2e7a3fd",
   "model": "claude-opus-4",
   "provider": "CLAUDE",
   "runtime_id": "claude-cli"
  },
  "skills": [
   {
    "package_digest": "f1a57a54e4bc4bb27de3e73920cc8dbef3571189ed3881f2ec95827f33f25320",
    "skill_id": "code-review",
    "version": "2.0.0"
   }
  ],
  "tools": [
   {
    "binding_id": "cbd_11V0G94S6N6VF1ZKQKYG9Y1HS4",
    "binding_version": "1.0.0",
    "capability": {
     "capability_id": "fs.read",
     "capability_version": "1.2.0"
    },
    "tool": {
     "implementation_digest": "6b26c87d9fddfbec46aaec4d907bf0b7b02d58bf6041da758395739f99e7dd4d",
     "tool_id": "ripgrep"
    }
   }
  ],
  "verifier": {
   "verification_spec_hash": "d3f922a310a02725de5f9b2a75e3766c634df4b6a6ac4e806f64a2224a4a582d",
   "verifier": {
    "implementation_digest": "9686223298b97bb181aeb771c256208e2a4d2a321bf5fdde2f829250e93dc22e",
    "verifier_contract_id": "diff-and-suite"
   },
   "version": "4.2.0"
  },
  "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C"
 },
 "node_contract": {
  "allowed_risk_class": "MEDIUM_DIGITAL",
  "content_hash": "d1f8450934cf84563fda0b0614e569e34bc3fd292d16e6a2e3ce23748f41e710",
  "contract_id": "nct_7V8J5MNFXNMKS70G8S6S43RRGC",
  "created_at": "2026-03-01T09:00:00Z",
  "created_by": {
   "display_name": "v0.4 contract freeze fixture",
   "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
   "status": "ACTIVE"
  },
  "execution_instance_id": "run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
  "failure_policy_ref": {
   "content_digest": "508096894efefd9fbf67fd47a509d3e3ff9100ff53bcdba000ef8f5e1cbade91",
   "policy_id": "workspace-failure-policy",
   "version": "1.4.0"
  },
  "graph_revision": 4,
  "node_id": "implement-migration",
  "node_kind": "TASK",
  "objective": "Write the additive migration and prove it reverses.",
  "objective_contract_ref": {
   "approved_at": "2026-02-01T08:00:00Z",
   "approved_by": {
    "display_name": "v0.4 contract freeze fixture",
    "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    "status": "ACTIVE"
   },
   "content_hash": "eeb604d2b673fc4518f6cc1028e1a3d67f72cc7ccd2110ce36b2c2f978371de9",
   "contract_id": "objective-contract-ref-embedded",
   "contract_type": "accretion.objective-contract-ref",
   "created_at": "2026-03-01T09:00:00Z",
   "created_by": {
    "display_name": "v0.4 contract freeze fixture",
    "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    "status": "ACTIVE"
   },
   "labels": {},
   "objective_contract_hash": "13f224df431229df61136c206eb2a5857df8c6a2907a3c3697192210aafc0ad4",
   "objective_contract_id": "obj_HQ5A32JBQ5CTEZTZWCVPPPQ4JM",
   "objective_contract_ref": null,
   "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
   "retention_class": null,
   "revision": 3,
   "risk_policy": {
    "content_digest": "b941fb5563b0aabec00a46d9150fff0d85e017346a26e12467ecdb80ffa458fd",
    "policy_id": "workspace-risk-policy",
    "version": "3.1.0"
   },
   "schema_version": "1.0.0",
   "supersedes_contract_id": null,
   "utility_profile_id": "balanced-delivery",
   "verified_success_floor": 0.9,
   "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C"
  },
  "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
  "resource_cap": {
   "maximum_attempts": 3,
   "maximum_cost": "12.50",
   "maximum_latency_ms": 900000,
   "maximum_tool_calls": 200
  },
  "run_graph_id": "rgr_5A1MZBVATRT3C1SAWW530FQGMX",
  "verification_spec_ref": {
   "content_hash": "d3f922a310a02725de5f9b2a75e3766c634df4b6a6ac4e806f64a2224a4a582d",
   "verification_spec_id": "vsp_9XSJHS3RTG3H32AJ1E1DF2313C"
  },
  "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C"
 },
 "objective_contract": {
  "approval_receipt_ref": {
   "digest": "f0b1cd79e1ea7de0cacfc6067cbe1748a26dbaba84d68a33398e59c15b245367",
   "media_type": "application/pdf",
   "retention_class": "STANDARD",
   "uri": "https://approvals.example.test/receipts/2026-02-01"
  },
  "content_hash": "f27d701fc5d2567a673d5fb195729e8a5366a6cafe9f61f1f134b4eb5346febb",
  "contract_id": "obj_1YWV9H9QDV4D7S8EQ2J7M91K1Y",
  "created_at": "2026-03-01T09:00:00Z",
  "created_by": {
   "display_name": "v0.4 contract freeze fixture",
   "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
   "status": "ACTIVE"
  },
  "false_acceptance_ceiling": 0.02,
  "goal": "Ship the routing freeze with no behaviour change.",
  "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
  "resource_budget": {
   "maximum_attempts": 3,
   "maximum_cost": "12.50",
   "maximum_latency_ms": 900000,
   "maximum_tool_calls": 200
  },
  "revision": 1,
  "risk_policy_ref": {
   "content_digest": "b941fb5563b0aabec00a46d9150fff0d85e017346a26e12467ecdb80ffa458fd",
   "policy_id": "workspace-risk-policy",
   "version": "3.1.0"
  },
  "scope_in": [
   "contracts",
   "fixtures"
  ],
  "utility_weights": {
   "cost": 0.25,
   "latency": 0.15,
   "quality": 0.6
  },
  "verified_success_floor": 0.9,
  "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C"
 },
 "routing_context": {
  "available_runtime_snapshot_id": "mcp_QF1HMTBMNB36GQHWT8JHYDEGP6",
  "capability_registry_snapshot_id": "mcp_SMCJYH1VRSCC2HHMXFHYPXNP90",
  "connection_availability_snapshot_id": "mcp_VXPW2590C7M84VNRYE2XTBX3HB",
  "content_hash": "e87acbf48c1b10c207249a7d3df8effde7454db273aa19677a8c5eaae24c413e",
  "contract_id": "rrq_MMVBSSHWNGSA9RK2H5XFVBW8D1",
  "created_at": "2026-03-01T09:00:00Z",
  "created_by": {
   "display_name": "v0.4 contract freeze fixture",
   "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
   "status": "ACTIVE"
  },
  "graph_features": {
   "child_node_types": [
    "VERIFIER"
   ],
   "critical_path": true,
   "depth": 3,
   "parent_node_types": [
    "TASK",
    "AGENT"
   ],
   "retry_number": 1
  },
  "node_contract_ref": {
   "immutable_hash": "432d52336d47b46491433a0186a7b62e9e382134874f526483d0e592aef92c4a",
   "node_contract_id": "nct_2GZRP05TEP5C4WPV6JFRTWFZNP"
  },
  "policy_snapshot_id": "pol_SHHZA9CFZ1EWF7Y7R6HDRH9K66",
  "project_features": {
   "contract_id": "project-features-nested-minimal",
   "created_at": "2026-03-01T09:00:00Z",
   "created_by": {
    "display_name": "v0.4 contract freeze fixture",
    "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    "status": "ACTIVE"
   },
   "feature_window_days": 90,
   "observed_task_count": 214,
   "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
   "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C"
  },
  "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
  "requested_at": "2026-03-01T08:59:30Z",
  "task_features": {
   "contract_id": "task-features-nested-minimal",
   "created_at": "2026-03-01T09:00:00Z",
   "created_by": {
    "display_name": "v0.4 contract freeze fixture",
    "principal_id": "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    "status": "ACTIVE"
   },
   "expected_horizon": "MEDIUM",
   "irreversible_actions": false,
   "profile_confidence": 0.78,
   "project_id": "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
   "risk": "MEDIUM",
   "source_profile_id": "prf_060BKJW56KVJ0VGEPVFN37HCKZ",
   "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C"
  },
  "workspace_id": "wks_8G33T24F686H6EJPBHRSFYCC3C",
  "workspace_router_version": "rmv_D0Q67B7EW89E1W1KJJFEC3XVN5"
 }
}
"""
"""The golden v0.4 fixture documents, byte-for-byte, as the drill's probe.

``tests/fixtures/contracts/v0.4/{routing_context,execution_configuration,node_contract,
objective_contract}/minimal.json``, and a test in ``tests/test_v04_m8_promotion.py``
asserts that equality so that the copy cannot rot. They live here as JSON rather than as a
package data file or a keyword-constructed object for two reasons: the ``content_hash`` in
each one is checked on validation, so a contract change that moved a field fails loudly
instead of quietly changing what the drill measures; and a hand-built equivalent would have
had to re-derive four nested value-object trees whose only correct value is the one the
freeze already committed.
"""

__all__ = [
    "ACTIVATION_PURPOSE_LABEL",
    "ADAPTER_VERSION",
    "COHORT_LABEL",
    "CORRECTNESS_COHORT",
    "DRILL_DIGEST_LABEL",
    "HIGH_RISK_COHORT",
    "POLICY_COHORT",
    "SECRETS_COHORT",
    "VERIFIER_CONFLICT_COHORT",
    "ActivationConflictError",
    "HoldoutLeakageError",
    "PromotionError",
    "PromotionEvaluator",
    "PromotionNotApprovedError",
    "PromotionReportConflictError",
    "PromotionService",
    "RollbackDrill",
    "RollbackDrillError",
    "build_promotion_evaluator",
    "build_promotion_service",
    "evaluation_cohorts",
]
