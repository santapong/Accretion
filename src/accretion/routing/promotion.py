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
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from hashlib import sha256
from typing import Any

from accretion.contracts import EventType, PrincipalRef, PrincipalStatus, Provider
from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import (
    ExecutionConfiguration,
    NodeContract,
    ObjectiveContract,
    RouterActivation,
    RouterActivationKind,
    RouterModelVersion,
    RouterPromotionDecision,
    RouterPromotionReport,
    RouterStatus,
    RoutingContext,
)
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.activation import ActivationLedger, family_key_for
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.errors import RoutingError
from accretion.routing.features import EvidenceSummary, Vocabulary, featurize
from accretion.routing.train import IDEMPOTENCY_LABEL, LearnedPredictorLoader
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
    "DRILL_DIGEST_LABEL",
    "ActivationConflictError",
    "PromotionError",
    "PromotionNotApprovedError",
    "PromotionService",
    "RollbackDrill",
    "RollbackDrillError",
    "build_promotion_service",
]
