"""SDD §9.4's cold-start scorer: the learned prior, the project adapter, and the capped prior.

This is the first module that lets anything learned reach a routing decision. Everything
about it is arranged so that the *least* it can do is reproduce M2 exactly, and the most it
can do is bounded by two rules stated in the SDD as open questions and resolved here.

**OQ-406 — the adapter corrects, it does not predict.** A project adapter is applied as a
shift on the workspace prior's *logit*, and the same shift moves the mean and the lower
bound together (:meth:`ColdStartScorer._adapt`). Moving only the mean would let a project's
own history raise a candidate's expected value while leaving the bound that guards §9.5's
safe set untouched, which is a way of passing the gate by not being measured by it. Moving
them together means a project can be optimistic *and* say so at the bound, or it can be
pessimistic, but it cannot be the first while presenting as the second.

**OQ-408 — cross-domain evidence is a capped prior mean and never a count.** A record whose
contract signature does not match the node's is counted by
:func:`~accretion.routing.features.summarize_evidence` in ``n_cross_domain`` and in nothing
else, so it never reaches the model as in-domain history. What it may do is pull the
predicted mean toward the rate observed out of domain, by at most
:data:`CROSS_DOMAIN_CAP` of the way (:func:`cross_domain_prior`). The lower confidence bound
is not touched at all. Those two sentences together are AC4-M5-021: the §9.5 gate reads
``lower_confidence_success``, so no quantity of cross-domain evidence can move a candidate
across the gate, and the gate is the only thing that stands between a candidate and live
routing. A property test draws five hundred configurations of that claim.

**Degradation is a ladder, not an exception.** §15.1 lists five ways this module can lose an
input and prescribes a different, weaker answer for each. None of them is an error: a router
that raised when its model was missing would take a run down over a component the SDD says
to route without. Every failure path here ends in a
:class:`~accretion.routing.stages.ScoredSlate`, and the ones that lost something say so in
:data:`~accretion.routing.stages.DEGRADED_LABEL`, which the service merges into the receipt.
The most consequential loss — no loadable prior, or a prior fitted under another vocabulary
— returns the cold-start slate untouched, which *is* the audited deterministic baseline,
because that is exactly what the candidates already carry when they arrive.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from accretion.contracts.routing import (
    ConfigurationCandidate,
    ConstructionStage,
    ContractSignature,
    DistributionEstimate,
    ExperienceRecord,
    NodeContract,
    ObjectiveContract,
    PredictedOutcomes,
    RouterModelVersion,
    RoutingContext,
    VerificationState,
)
from accretion.routing.adapter import AdapterArtifact, ProjectAdapter
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.features import (
    EvidenceSummary,
    Vocabulary,
    featurize,
    summarize_evidence,
)
from accretion.routing.ranker import OutcomePredictor
from accretion.routing.selector import COLD_START_PRIOR_METHOD, _rebuild_candidate
from accretion.routing.stages import (
    ADAPTER_UNAVAILABLE,
    DEGRADED_LABEL,
    VOCABULARY_MISMATCH,
    WORKSPACE_MODEL_UNAVAILABLE,
    ActiveVersions,
    ScoredSlate,
    node_signature,
    worst_degradation,
)
from accretion.routing.train import LearnedPredictorLoader, RouterTrainingError

CROSS_DOMAIN_CAP = 0.15
"""OQ-408's answer: at most 15% of the predicted mean may come from out-of-domain evidence.

A fixed constant and not a tuned parameter. §9.4 says cross-domain evidence "receives a
capped prior weight and cannot directly enable live routing", and a cap that moved with the
data would be a cap the data could raise. 0.15 is the value the open question records; the
property test does not depend on its exact size, only on the cap being applied and on the
lower bound being outside it.
"""

CROSS_DOMAIN_K = 20.0
"""The half-trust count of the shrinkage that feeds the cap: ``n / (n + k)``.

Shared shape with :func:`~accretion.routing.adapter.influence` and for the same reason: the
first out-of-domain record must not weigh as much as the hundredth. ``k`` only decides how
fast the weight climbs *toward* :data:`CROSS_DOMAIN_CAP`; it can never exceed it, so this
number cannot change what AC4-M5-021 asserts.
"""

MAXIMUM_EXPERIENCE_REFS = 64
"""``RoutingDecisionReceipt.experience_refs`` is bounded at 64 by the contract.

Truncation is by sorted id and not by relevance, because a "most relevant 64" would be a
second, unversioned ranking hidden inside a citation list.
"""

VOCABULARY = Vocabulary()
"""The token table this scorer featurizes under, and the one a prior must have been fitted on.

Empty, which is the honest state before M6 freezes a real one: every model id and adapter
version maps to ``OTHER``. A prior trained over a populated vocabulary means something
different by the same two columns, which is why a digest mismatch degrades rather than
predicts — see :data:`~accretion.routing.stages.VOCABULARY_MISMATCH`.
"""

_LOGIT_CLAMP = 30.0
"""Where :func:`logit` saturates. ``sigmoid(30)`` is 1 to fourteen decimal places."""

VOCABULARY_DIGEST_LABEL = "vocab_digest"
"""The label a :class:`~accretion.contracts.routing.RouterTrainingSnapshot` records its
vocabulary digest under (``routing/training_snapshot.py``). Read rather than recomputed:
:class:`~accretion.routing.ranker.RankerArtifact` stores no vocabulary, so the snapshot the
version was fitted from is the only place the table it learned under is written down.
"""


def logit(probability: float) -> float:
    """The log-odds of ``probability``, saturating rather than diverging at either end.

    A calibrated head can legitimately return exactly 0 or 1, and an infinite logit would
    propagate through the adapter as a ``nan`` the moment it met a finite shift. Clamping is
    the behaviour :func:`~accretion.routing.gbdt.sigmoid` already assumes on the way back.
    """

    bounded = min(1.0, max(0.0, probability))
    if bounded <= 0.0:
        return -_LOGIT_CLAMP
    if bounded >= 1.0:
        return _LOGIT_CLAMP
    return max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, math.log(bounded / (1.0 - bounded))))


def expit(value: float) -> float:
    """The inverse of :func:`logit`, on the same clamped domain."""

    clamped = max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, value))
    if clamped >= 0.0:
        return 1.0 / (1.0 + math.exp(-clamped))
    shifted = math.exp(clamped)
    return shifted / (1.0 + shifted)


def cross_domain_prior(
    mean: float,
    *,
    evidence: EvidenceSummary,
    rate: float | None,
    cap: float = CROSS_DOMAIN_CAP,
    k: float = CROSS_DOMAIN_K,
) -> float:
    """Blend ``rate`` into ``mean`` with weight ``min(cap, n_cross / (n_cross + k))``.

    Returns ``mean`` unchanged when there is no out-of-domain evidence or no rate to blend,
    and never moves it by more than ``cap`` of the distance to ``rate``. **The lower
    confidence bound is not an argument to this function**, which is the whole of OQ-408:
    the §9.5 safe set is defined on the bound, so a quantity this function cannot see is a
    quantity that cannot enable live routing.

    Raises ``ValueError`` for a cap outside ``[0, 1]`` or a non-positive ``k``: a cap of one
    would be no cap, and a ``k`` of zero would give the first out-of-domain record the full
    capped weight.
    """

    if not 0.0 <= cap <= 1.0:
        raise ValueError(f"cap must lie in [0, 1], got {cap}")
    if not math.isfinite(k) or k <= 0.0:
        raise ValueError(f"k must be a positive, finite half-trust count, got {k}")
    if rate is None or evidence.n_cross_domain <= 0:
        return mean
    n_cross = float(evidence.n_cross_domain)
    weight = min(cap, n_cross / (n_cross + k))
    return (1.0 - weight) * mean + weight * rate


class ColdStartScorer:
    """SDD §9.3 outcome estimation, as far as M5's inputs allow it to go.

    Holds a loader, an artefact store and a clock, and nothing about one decision: the
    versions in force, the candidates and the evidence all arrive as arguments, so two calls
    with the same arguments produce the same slate regardless of which scorer object made it.

    The loader is :class:`~accretion.routing.train.LearnedPredictorLoader` and not a
    predictor, because AC4-M4-016 makes that class the only door a learned predictor comes
    through: a scorer handed an already-assembled predictor would be a second door, and the
    refusal M4 spent a milestone building would be one import away from being bypassed.
    """

    def __init__(
        self,
        loader: LearnedPredictorLoader,
        artifacts: ArtifactStore,
        clock: Callable[[], datetime],
    ) -> None:
        self.loader = loader
        self.artifacts = artifacts
        self.clock = clock

    async def score(
        self,
        *,
        context: RoutingContext,
        candidates: Sequence[ConfigurationCandidate],
        node: NodeContract,
        objective: ObjectiveContract,
        versions: ActiveVersions,
        evidence_by_hash: Mapping[str, Sequence[ExperienceRecord]],
    ) -> ScoredSlate:
        """Re-score every candidate under the active prior, or degrade to the prior slate."""

        evidence_ids = tuple(
            sorted(
                {
                    record.contract_id
                    for records in evidence_by_hash.values()
                    for record in records
                }
            )
        )[:MAXIMUM_EXPERIENCE_REFS]

        predictor, degraded = await self._predictor(versions)
        if predictor is None:
            return ScoredSlate(
                candidates=tuple(candidates),
                calibration_version=COLD_START_PRIOR_METHOD,
                evidence_ids=evidence_ids,
                labels=self._labels(degraded),
            )

        adapter, degraded = await self._adapter(versions, degraded)
        signature = node_signature(node, objective_digest=objective.content_hash)
        as_of = self.clock()
        scored: list[ConfigurationCandidate] = []
        calibration_version = COLD_START_PRIOR_METHOD
        for candidate in candidates:
            records = list(
                evidence_by_hash.get(candidate.configuration.configuration_hash, ())
            )
            summary = summarize_evidence(
                records,
                signature=signature,
                configuration_hash=candidate.configuration.configuration_hash,
                as_of=as_of,
            )
            row = featurize(
                context, candidate.configuration, node, objective, summary, VOCABULARY
            )
            predicted, uncertainty = predictor.predict(row.values)
            calibration_version = uncertainty.calibration_version
            success = self._success_estimate(
                predicted.node_verified_success,
                summary=summary,
                records=records,
                signature=signature,
                adapter=adapter,
            )
            scored.append(
                _rebuild_candidate(
                    candidate,
                    construction_stage=ConstructionStage.PREDICT_OUTCOME,
                    predicted=PredictedOutcomes(
                        quality=predicted.quality,
                        cost=predicted.cost,
                        latency=predicted.latency,
                        node_verified_success=success,
                        run_verified_success=predicted.run_verified_success,
                    ),
                    uncertainty_score=uncertainty.epistemic_uncertainty,
                    lower_confidence_success=success.lower_bound,
                )
            )
        return ScoredSlate(
            candidates=tuple(scored),
            calibration_version=calibration_version,
            evidence_ids=evidence_ids,
            labels=self._labels(degraded),
        )

    # ------------------------------------------------------------------ the ladder

    async def _predictor(
        self, versions: ActiveVersions
    ) -> tuple[OutcomePredictor | None, str | None]:
        """Assemble the active workspace prior, or name the §15.1 rung that stopped it."""

        if versions.router_version_id is None:
            return None, WORKSPACE_MODEL_UNAVAILABLE
        try:
            version = await self.loader.store.get_router_model_version(
                versions.router_version_id
            )
            if version is None:
                return None, WORKSPACE_MODEL_UNAVAILABLE
            predictor = self.loader.assemble(version)
        except (KeyError, ValueError, RouterTrainingError, OSError):
            # §15.1 "workspace model unavailable: use deterministic baseline". Every
            # refusal the loader can raise — never evaluated, digest drift, a decode
            # failure, a missing file — has the same prescribed answer, and catching them
            # individually would only make it possible to miss one.
            return None, WORKSPACE_MODEL_UNAVAILABLE
        if not await self._vocabulary_matches(version):
            return None, VOCABULARY_MISMATCH
        return predictor, None

    async def _vocabulary_matches(self, version: RouterModelVersion) -> bool:
        """Whether the snapshot ``version`` was fitted from names this scorer's token table.

        A missing snapshot is a mismatch and not an error: the vocabulary is unverifiable,
        and §7.12's rule is that unverifiable is refused rather than assumed.
        """

        snapshot = await self.loader.store.get_router_training_snapshot(
            version.training_snapshot_id
        )
        if snapshot is None:
            return False
        return snapshot.labels.get(VOCABULARY_DIGEST_LABEL) == VOCABULARY.digest()

    async def _adapter(
        self, versions: ActiveVersions, degraded: str | None
    ) -> tuple[AdapterArtifact | None, str | None]:
        """Load the project adapter, or record §15.1's "use workspace prior, reduced confidence".

        The bytes are addressed by the adapter version's ``calibration_artifact_digest``,
        which is where :func:`~accretion.routing.adapter.artifact_digest` documents an
        adapter artefact's digest is recorded: ADR-047 keeps the calibration layer's identity
        separate from the ranker's, and a residual is the calibration layer.
        """

        if versions.adapter_version_id is None:
            return None, worst_degradation(degraded, ADAPTER_UNAVAILABLE)
        try:
            version = await self.loader.store.get_router_model_version(
                versions.adapter_version_id
            )
            if version is None:
                return None, worst_degradation(degraded, ADAPTER_UNAVAILABLE)
            artifact = AdapterArtifact.model_validate_json(
                self.artifacts.load(version.calibration_artifact_digest)
            )
        except (KeyError, ValueError, OSError):
            return None, worst_degradation(degraded, ADAPTER_UNAVAILABLE)
        return artifact, degraded

    # ------------------------------------------------------------------ the estimate

    def _success_estimate(
        self,
        estimate: DistributionEstimate,
        *,
        summary: EvidenceSummary,
        records: Sequence[ExperienceRecord],
        signature: ContractSignature,
        adapter: AdapterArtifact | None,
    ) -> DistributionEstimate:
        """Apply OQ-406's adapter shift and then OQ-408's capped prior, in that order.

        The order matters and is not arbitrary. The adapter corrects the *prior's* logit, so
        it must see the prior's number; the cross-domain blend is a statement about how much
        of the final mean may come from elsewhere, so it applies to whatever the in-domain
        machinery concluded. Reversing them would let the adapter re-amplify the capped
        contribution and the cap would no longer bound the result.
        """

        mean, lower = self._adapt(estimate, summary=summary, adapter=adapter)
        blended = cross_domain_prior(
            mean,
            evidence=summary,
            rate=self._cross_rate(records, signature),
        )
        # The bound is the prior's, shifted only by the adapter. `max` and not an assertion:
        # a blend that pulled the mean below the untouched bound is a legitimate outcome of
        # pessimistic out-of-domain evidence, and the estimate stays well formed by keeping
        # the mean at the bound rather than by raising the bound to the mean.
        centre = min(1.0, max(lower, blended))
        return DistributionEstimate(
            mean=centre,
            lower_bound=lower,
            upper_bound=max(centre, min(1.0, estimate.upper_bound)),
            confidence=estimate.confidence,
            method=estimate.method,
        )

    @staticmethod
    def _adapt(
        estimate: DistributionEstimate,
        *,
        summary: EvidenceSummary,
        adapter: AdapterArtifact | None,
    ) -> tuple[float, float]:
        """The mean and the lower bound, both shifted by the *same* logit delta (OQ-406).

        ``n_project`` is ``n_same_signature`` — the project's in-domain count at serving
        time — and never ``n_cross_domain``, which is the other half of AC4-M5-021: an
        adapter whose influence grew with out-of-domain evidence would be a second route
        from cross-domain history to the lower bound.
        """

        if adapter is None:
            return estimate.mean, estimate.lower_bound
        prior_logit = logit(estimate.mean)
        delta = (
            ProjectAdapter.apply(adapter, prior_logit, n_project=summary.n_same_signature)
            - prior_logit
        )
        adapted_mean = expit(prior_logit + delta)
        adapted_lower = expit(logit(estimate.lower_bound) + delta)
        # `min` only defends the clamp: `logit` saturates at ±30, so two inputs that were
        # ordered before the shift can land on the same clamped value after it, and a bound
        # a hair above its mean would fail `DistributionEstimate`'s bracket validator.
        return adapted_mean, min(adapted_lower, adapted_mean)

    @staticmethod
    def _cross_rate(
        records: Sequence[ExperienceRecord], signature: ContractSignature
    ) -> float | None:
        """The verified-success rate over the records the summary counted as out of domain.

        Computed here and not in :func:`~accretion.routing.features.summarize_evidence`,
        which deliberately computes no aggregate over cross-domain rows. Keeping it out of
        the summary keeps it out of the feature row, so the rate reaches exactly one
        consumer — the capped blend — and reaches the model itself never.
        """

        out_of_domain = [
            record for record in records if record.contract_signature != signature
        ]
        if not out_of_domain:
            return None
        return sum(
            1
            for record in out_of_domain
            if record.local_verification_status is VerificationState.PASS
        ) / len(out_of_domain)

    @staticmethod
    def _labels(degraded: str | None) -> Mapping[str, str]:
        return {} if degraded is None else {DEGRADED_LABEL: degraded}


__all__ = [
    "CROSS_DOMAIN_CAP",
    "CROSS_DOMAIN_K",
    "MAXIMUM_EXPERIENCE_REFS",
    "VOCABULARY",
    "VOCABULARY_DIGEST_LABEL",
    "ColdStartScorer",
    "cross_domain_prior",
    "expit",
    "logit",
]
