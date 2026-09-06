"""Turning a workspace's stored evidence into one frozen :class:`BreakerInput`.

:mod:`accretion.routing.breakers` is six pure predicates over frozen numbers and says, in as
many words, that *sampling* those numbers is somebody else's job. This module is that job, and
keeping it here is what lets the predicates be exercised at both sides of every threshold
without a store in the room.

**Every field is measured, and where it cannot be measured it is failed closed.** A calibration
report that will not load, an ACTIVE version with no training snapshot, a node class with no
recent verification history: each of those is an *absence of evidence about safety*, and §15.3's
own doctrine ("fail closed, twice") is that an absence trips. So a missing calibration report
becomes ``ece_recent = 1.0`` rather than a tolerant default, a version with no declared boundary
window leaves ``version_boundaries`` empty and every serving component drifts, and a node class
with no recent verified work reports zero coverage. None of those is a special case inside a
predicate; they are ordinary values that happen to be on the wrong side of a line.

**The window is a node class's own recent work.** Verification results are keyed by execution
instance and carry no node kind, so the window is taken over the *experience records* of the
class — which do carry the contract signature — and the verification results are then joined to
those executions. A workspace-wide rate would let a healthy class inherit a sick one's
false-acceptance rate and, worse, let a sick class hide behind a healthy one's volume.

**Nothing here decides anything.** The sampler reads; :func:`~accretion.routing.breakers.
exploration_allowed` decides. A sampler that suppressed a field it thought uninteresting would
be making the decision one layer early and out of sight of the audit that reads the verdicts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from accretion.contracts.routing import (
    ExperienceRecord,
    IndependentVerificationResult,
    RouterModelVersion,
    RouterPromotionReport,
    VerificationState,
)
from accretion.persistence.store import StateStore
from accretion.routing.breakers import BreakerInput
from accretion.routing.train import LearnedPredictorLoader

UNCALIBRATED_ECE = 1.0
"""The expected calibration error attributed to a router whose report cannot be read.

The maximum, so :func:`~accretion.routing.breakers.calibration_exceeded` trips against any
ceiling below 1.0. A router whose calibration nobody can read is not a calibrated router.
"""


@dataclass(frozen=True, slots=True)
class BreakerSamplerConfig:
    """The thresholds and the observed world one sample is taken against.

    Split by *ownership*, which is why it is one object rather than eight parameters. The two
    rate bounds come from the objective (a human approved them), ``max_ece`` and ``delta_ni``
    from the deployment's :class:`~accretion.routing.bandit.BanditConfig`, and
    ``serving_versions`` and ``policy_snapshot_resolved`` from the routing snapshot — the
    observation of the world this decision is being made in, which the sampler must not go
    and re-observe for itself or the receipt would pin one world and the breakers another.

    ``router_version_id`` is ``None`` for a workspace with no active learned router. The
    sample is still taken: the calibration and drift fields then report the absence, which is
    the honest reading of "no version is serving" and the one that fails closed.
    """

    router_version_id: str | None
    serving_versions: Mapping[str, str]
    policy_snapshot_resolved: bool
    false_acceptance_ceiling: float
    coverage_floor: float
    max_ece: float
    delta_ni: float
    recent_window: int
    project_id: str | None = None
    critical_cohorts_only: bool = field(default=True)


class BreakerSampling(Protocol):
    """What a guarded bandit needs of a sampler: one frozen input per decision.

    A protocol rather than the concrete class, so that a test can drive the exploration path
    with an input it states outright — the interesting breaker cases are combinations of six
    numbers, and reaching them through a store would bury the case under fixtures.
    """

    async def sample(
        self, *, workspace_id: str, node_class: str, config: BreakerSamplerConfig
    ) -> BreakerInput:
        """One decision's worth of evidence for ``(workspace_id, node_class)``."""
        ...


class BreakerSampler:
    """Read the six breakers' evidence out of the store, in four queries and no scans.

    ``loader`` is a :class:`~accretion.routing.train.LearnedPredictorLoader` and not an
    :class:`~accretion.routing.artifacts.ArtifactStore`, because the calibration report is
    digest-pinned by the version that claims it and the loader is the only door that checks
    the pin (AC4-M4-016). Reading the bytes directly would let a report that no longer hashes
    to its digest quietly satisfy a safety breaker.
    """

    def __init__(self, store: StateStore, loader: LearnedPredictorLoader) -> None:
        self.store = store
        self.loader = loader

    async def sample(
        self, *, workspace_id: str, node_class: str, config: BreakerSamplerConfig
    ) -> BreakerInput:
        """Assemble one :class:`~accretion.routing.breakers.BreakerInput`.

        ``audit_probe_ok`` is not a separate service call. §15.3's sixth condition asks
        whether the audit trail is *available*, and the audit trail in this repository is the
        stored record this method has just finished reading; a probe that pinged something
        else would answer a question about a different system. So the reads below are the
        probe: if the store refused any of them, exploration is disabled for the reason
        §15.3 gives, and the deterministic baseline — which reads none of this — is
        untouched.
        """

        try:
            records = await self.store.list_experience_records(workspace_id=workspace_id)
            results = await self.store.list_verification_results(workspace_id=workspace_id)
            reports = await self.store.list_router_promotion_reports(workspace_id=workspace_id)
            version = await self._version(config.router_version_id)
        except Exception:
            return self._blind(config)

        window = self._window(records, node_class=node_class, size=config.recent_window)
        instances = {record.source_node_execution_id for record in window}
        recent = [
            result for result in results if result.execution_instance_id in instances
        ]
        lcbs, baselines = self._cohorts(reports, version=version, config=config)
        return BreakerInput(
            false_acceptance_rate_recent=self._false_acceptance_rate(recent),
            false_acceptance_ceiling=config.false_acceptance_ceiling,
            ece_recent=self._ece(version),
            max_ece=config.max_ece,
            cohort_lcbs=lcbs,
            cohort_baselines=baselines,
            delta_ni=config.delta_ni,
            serving_versions=dict(config.serving_versions),
            version_boundaries=await self._boundaries(version),
            verification_coverage_recent=self._coverage(recent),
            coverage_floor=config.coverage_floor,
            policy_snapshot_resolved=config.policy_snapshot_resolved,
            audit_probe_ok=True,
        )

    # -- the reads ---------------------------------------------------------------------

    async def _version(self, version_id: str | None) -> RouterModelVersion | None:
        if version_id is None:
            return None
        return await self.store.get_router_model_version(version_id)

    @staticmethod
    def _blind(config: BreakerSamplerConfig) -> BreakerInput:
        """The input a sampler that could not read the audit record must return.

        Every field is on the tripping side of its own threshold, not merely
        ``audit_probe_ok=False``: a caller that reported only the probe would be claiming to
        have measured five other things it did not measure, and a later reader of the verdict
        would take those five silences for five clean bills of health.
        """

        return BreakerInput(
            false_acceptance_rate_recent=1.0,
            false_acceptance_ceiling=config.false_acceptance_ceiling,
            ece_recent=UNCALIBRATED_ECE,
            max_ece=config.max_ece,
            cohort_lcbs={},
            cohort_baselines={},
            delta_ni=config.delta_ni,
            serving_versions=dict(config.serving_versions),
            version_boundaries={},
            verification_coverage_recent=0.0,
            coverage_floor=config.coverage_floor,
            policy_snapshot_resolved=config.policy_snapshot_resolved,
            audit_probe_ok=False,
        )

    @staticmethod
    def _window(
        records: Sequence[ExperienceRecord], *, node_class: str, size: int
    ) -> list[ExperienceRecord]:
        """The ``size`` most recent records of ``node_class``, newest first, deterministically.

        Sorted by ``(created_at, contract_id)`` and then reversed rather than by ``created_at``
        alone, for the reason every other list in this package gives: two records written in
        one transaction share an instant, and a window that took whichever the backend
        returned first would sample two different sets on two backends.
        """

        matching = [
            record
            for record in records
            if record.contract_signature.node_kind.value == node_class
        ]
        matching.sort(key=lambda item: (item.created_at, item.contract_id), reverse=True)
        return matching[:size]

    # -- the six fields ----------------------------------------------------------------

    @staticmethod
    def _false_acceptance_rate(results: Sequence[IndependentVerificationResult]) -> float:
        """The fraction of *acceptances* that a conflict was later recorded against.

        The denominator is the acceptances and not every result, because a false acceptance is
        an acceptance that turned out wrong: dividing by the rejections too would let a node
        class hide a bad acceptance rate behind a run of honest refusals. A window with no
        acceptances in it has no rate, and the honest value for that is 0.0 — nothing was
        wrongly accepted because nothing was accepted — with ``verification_coverage_recent``
        carrying the fact that little was verified.
        """

        accepted = [
            result for result in results if result.status is VerificationState.PASS
        ]
        if not accepted:
            return 0.0
        wrong = sum(1 for result in accepted if result.conflict_refs)
        return wrong / len(accepted)

    @staticmethod
    def _coverage(results: Sequence[IndependentVerificationResult]) -> float:
        """Mean claim coverage over the window; 0.0 when nothing was verified at all.

        A result carrying no claim results contributes 0.0 rather than being skipped. A
        verifier that produced a verdict about no claims covered no claims, and skipping it
        would let a window of empty verdicts report the coverage of the one real one.
        """

        if not results:
            return 0.0
        per_result = [
            sum(claim.coverage for claim in result.claim_results) / len(result.claim_results)
            if result.claim_results
            else 0.0
            for result in results
        ]
        return sum(per_result) / len(per_result)

    def _ece(self, version: RouterModelVersion | None) -> float:
        """The ACTIVE version's ten-bin expected calibration error, or the maximum.

        ``UNCALIBRATED_ECE`` covers three different absences — no active version, a version
        whose report was never sealed, and bytes that no longer hash to the digest the version
        pinned — and they are one answer on purpose: each is a router whose confidence bounds
        cannot be shown to mean anything, and exploration reads those bounds.
        """

        if version is None:
            return UNCALIBRATED_ECE
        try:
            return self.loader.read_calibration_report(version).ece_10bin
        except Exception:
            return UNCALIBRATED_ECE

    @staticmethod
    def _cohorts(
        reports: Sequence[RouterPromotionReport],
        *,
        version: RouterModelVersion | None,
        config: BreakerSamplerConfig,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """The critical cohorts of the report that promoted this version, as two mappings.

        ``lcb`` is ``baseline_value + delta_lower_bound`` and not ``candidate_value``: §15.3's
        third condition compares a *lower confidence bound* against the baseline, and a point
        estimate handed in as a bound would let a cohort that regressed within its own
        interval pass. The pair is what
        :func:`~accretion.routing.breakers.critical_cohort_regression` needs, and a cohort
        present in ``lcbs`` and absent from ``baselines`` trips by design — which is why both
        are built in one pass over one report rather than merged from two.

        The *latest* report for the version, by ``(created_at, contract_id)``: a version can be
        evaluated more than once and the older verdict describes evidence that has since been
        added to.
        """

        if version is None:
            return {}, {}
        matching = [
            report
            for report in reports
            if report.candidate_version == version.contract_id
        ]
        if not matching:
            return {}, {}
        latest = max(matching, key=lambda item: (item.created_at, item.contract_id))
        lcbs: dict[str, float] = {}
        baselines: dict[str, float] = {}
        for cohort in latest.cohort_results:
            if config.critical_cohorts_only and not cohort.critical:
                continue
            comparison = cohort.comparison
            lcbs[cohort.cohort_id] = comparison.baseline_value + comparison.delta_lower_bound
            baselines[cohort.cohort_id] = comparison.baseline_value
        return lcbs, baselines

    async def _boundaries(
        self, version: RouterModelVersion | None
    ) -> dict[str, tuple[str, str]]:
        """The training snapshot's provider windows, as the inclusive pairs §15.3 compares to.

        ``RouterTrainingSnapshot.provider_version_boundaries`` records *one* string per
        provider — the version the snapshot's evidence was produced under — so the validated
        window is that version and nothing else, spelled ``(v, v)``. Widening it to an open
        interval here would validate versions nobody trained against, which is the drift the
        breaker is named after.
        """

        if version is None:
            return {}
        snapshot = await self.store.get_router_training_snapshot(version.training_snapshot_id)
        if snapshot is None:
            return {}
        return {
            provider: (boundary, boundary)
            for provider, boundary in snapshot.provider_version_boundaries.items()
        }


__all__ = [
    "UNCALIBRATED_ECE",
    "BreakerSampler",
    "BreakerSamplerConfig",
    "BreakerSampling",
]
