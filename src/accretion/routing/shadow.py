"""Shadow evaluation: a registered SHADOW version, what it would have chosen, and the paired
evidence a promotion is allowed to cite (SDD §10.2, §11.4; ADR-046, ADR-060).

ADR-046 stages v0.4 as offline, then shadow, then guarded bandit. This module is the whole of
the middle stage's arithmetic, and it is one module on purpose: M6.2 renders the report over
HTTP and M8.2 gates a promotion on it, and two copies of "is this shadow non-inferior" would
be two answers on the day one of them was tuned.

**A shadow policy is a router version, not a flag.** :meth:`ShadowEvaluator.register` writes a
second :class:`~accretion.contracts.routing.RouterModelVersion` with ``status=SHADOW``,
``parent_version_id`` set to the CANDIDATE it descends from, and the candidate's two
evaluation digests copied onto it. Copying rather than referencing is what keeps AC4-M4-016
true one hop later: :meth:`~accretion.routing.train.LearnedPredictorLoader.require_evaluated`
reads labels off the version in hand, so a SHADOW row that merely pointed at its parent would
load without an evaluation of its own the moment anybody read it directly. The registration
itself calls ``require_evaluated`` on the parent first, so an unevaluated candidate never
acquires a shadow stage at all.

**A shadow decision is a record and never a dispatch.** :func:`record_shadow_decision` writes
one :class:`~accretion.contracts.routing.ShadowDecision` comparing an executed receipt with
the receipt the shadow version produced. Nothing here executes, cancels or re-routes anything;
the executed receipt is read, not touched. That is the property that makes the stage safe to
run against live traffic, and it is enforced by this module having no reference to a runtime,
a run manager or a routing service.

**Evidence is paired or it is not evidence.** :func:`paired_deltas` joins
:class:`~accretion.contracts.routing.ShadowRolloutResult` rows by ``(shadow_decision_id,
trial_index)`` and keeps only the ``(SHADOW, CONTROL)`` pairs that are complete. A lone arm is
dropped rather than scored against a pooled mean of the other arm: ADR-060 branches the run
precisely so the per-node difficulty both arms share cancels, and an unpaired arm is a
measurement of that difficulty rather than of the router. The count of dropped arms is
reported as a gate rather than hidden, because OQ-409's power analysis needs the number of
*complete* pairs and nothing else.

**The interval is clustered by decision.** Several trials of one shadow decision are several
looks at the same node under the same seed policy, so they are one cluster, and
:func:`~accretion.routing.stats.hierarchical_bootstrap` resamples decisions before it resamples
trials within them. Bootstrapping trials flat would narrow the interval by roughly the square
root of the cluster size and announce a difference that does not replicate. The seed is
mandatory and carried on :class:`ShadowReportConfig`, so two readers of the same rows compute
the same lower bound.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from accretion.contracts import EventType, PrincipalRef, Provider, StrictModel
from accretion.contracts.canonical import canonical_json
from accretion.contracts.routing import (
    RouterModelVersion,
    RouterStatus,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutKind,
    ShadowRolloutResult,
    UtilityWeights,
)
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.regret import Outcome, utility
from accretion.routing.stats import hierarchical_bootstrap
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    CALIBRATION_REPORT_LABEL,
    LearnedPredictorLoader,
)
from accretion.runtimes.common import make_event

SHADOW_ADAPTER_VERSION = "router-shadow-v1"
"""``AgentEvent.adapter_version`` for everything this module announces."""

SHADOW_BUDGET_LABEL_PREFIX = "shadow_budget_"
"""Every :class:`ShadowBudget` field is written to the version's labels under this prefix.

Labels and not a JSON blob on the version: ``RouterModelVersion`` is a sealed, append-only
record with no free-form object field, and ``labels`` is the one map the freeze gave it. The
prefix keeps the budget distinguishable from the training labels the parent contributed,
which is what lets M6.2 read a budget back without knowing which milestone wrote which key.
"""

SHADOW_PARENT_LABEL = "shadow_parent_version_id"
"""The candidate this shadow stage evaluates, repeated in the labels for queryability.

``parent_version_id`` is the authoritative field and this label always equals it. The
duplicate exists because ``list_router_model_versions`` returns whole documents and a reader
filtering a workspace's shadow stages by their candidate should not have to know that a
lineage column and a label can disagree — the validator below makes sure they cannot.
"""

DEFAULT_BOOTSTRAPS = 2_000
"""Replicates for the paired interval. Enough that the percentile bound is stable to ~1e-3."""

DEFAULT_ALPHA = 0.05
"""A two-sided 95% interval; :attr:`ShadowReport.delta_lcb` is its lower end."""

DEFAULT_LATENCY_BUDGET_MS = 60_000.0
"""The denominator that turns ``observed.latency_ms`` into the normalised ``latency`` term.

:func:`~accretion.routing.regret.utility` is defined over budget-relative cost and latency, and
:class:`~accretion.contracts.routing.ShadowRolloutResult` records milliseconds. The benchmark
does the same division against a pre-registered ``latency_budget_ms``
(``router_benchmark.py``), and this is the same knob with a default rather than a registered
constant, because a shadow stage is scored per node and the node's own
``ResourceBudget.maximum_latency_ms`` is the value a caller with one should pass.
"""

DEFAULT_MIN_PAIRED_RUNS = 30
"""The fewest complete pairs :func:`shadow_gate` will call evidence.

Not a power calculation — OQ-409 owns that — but the point below which the clustered
bootstrap is resampling a handful of decisions and its interval describes the resampling
rather than the router.
"""

DEFAULT_DELTA_NI = 0.0
"""The floor :attr:`ShadowReport.delta_lcb` must clear, in utility units.

Stated as a floor on the lower bound rather than as a margin subtracted from it, because that
is the form both consumers use: ``0.0`` is "the shadow is not worse with 95% confidence", and
a negative value is a deliberately tolerated non-inferiority margin.
"""


class ShadowError(RuntimeError):
    """Base class for every refusal in this module."""


class ShadowRegistrationError(ShadowError):
    """A router version cannot be given a shadow stage.

    Distinct from
    :class:`~accretion.routing.train.RouterNotEvaluatedError`, which says the version has no
    evaluation on record: this one says the version is the wrong *kind* of thing to shadow —
    already promoted, already retired, or already a shadow of something else.
    """


class ShadowBudget(StrictModel):
    """What one shadow policy is allowed to spend before it stops branching runs.

    ADR-060 pays for shadow evidence by forking the live run, which means the stage has a
    real, recurring cost and no natural stopping point: every executed decision is another
    opportunity to branch. The budget is therefore part of *registering* the policy rather
    than a runtime setting, so that the version row itself records what the workspace agreed
    to spend on it and an audit does not have to reconstruct the limit from the rollouts that
    happened to fit inside it.

    ``daily_cost_cap`` is compared against the sum of ``ShadowRolloutResult.budget_consumed``
    over a day, in the same units, and ``max_trials_per_day`` bounds the count independently
    because a policy whose forks are individually cheap can still saturate the executor.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    daily_cost_cap: float = Field(gt=0)
    max_trials_per_day: int = Field(ge=1)

    def labels(self) -> dict[str, str]:
        """The budget as ``RouterModelVersion.labels`` entries, deterministically ordered.

        Values are strings because ``labels`` is a ``dict[str, str]``; the round trip is
        exact for the integer and goes through :func:`repr` for the float, which is
        shortest-round-trip in CPython, so a budget read back from a version equals the one
        registered.
        """

        return {
            f"{SHADOW_BUDGET_LABEL_PREFIX}daily_cost_cap": repr(self.daily_cost_cap),
            f"{SHADOW_BUDGET_LABEL_PREFIX}max_trials_per_day": str(self.max_trials_per_day),
        }

    def digest(self) -> str:
        """SHA-256 over this budget's canonical JSON; part of the registered version's id."""

        return hashlib.sha256(canonical_json(self)).hexdigest()

    @classmethod
    def from_labels(cls, labels: dict[str, str]) -> ShadowBudget | None:
        """The budget a registered version carries, or ``None`` if it carries none."""

        cap = labels.get(f"{SHADOW_BUDGET_LABEL_PREFIX}daily_cost_cap")
        trials = labels.get(f"{SHADOW_BUDGET_LABEL_PREFIX}max_trials_per_day")
        if cap is None or trials is None:
            return None
        return cls(daily_cost_cap=float(cap), max_trials_per_day=int(trials))


class ShadowEvaluator:
    """Registers shadow stages. Holds no run state and dispatches nothing.

    Constructed with the same three collaborators the training service takes — the store, the
    artefact root and a clock — so that the API lifespan builds it beside
    :class:`~accretion.routing.train.RouterTrainingService` on one line. The artefact store is
    held rather than used on the registration path: ``register`` gates on labels, which
    :meth:`~accretion.routing.train.LearnedPredictorLoader.require_evaluated` reads without
    touching bytes, and M6.2's report assembles the predictor through the same loader.
    """

    def __init__(
        self,
        store: StateStore,
        artifacts: ArtifactStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.clock = clock
        self.loader = LearnedPredictorLoader(store, artifacts)

    async def register(
        self,
        *,
        candidate_version_id: str,
        budget: ShadowBudget,
        principal: PrincipalRef,
        workspace_id: str,
        run_id: str | None = None,
    ) -> RouterModelVersion:
        """Register ``candidate_version_id`` for shadow evaluation under ``budget``.

        Raises ``KeyError`` when the candidate does not exist *or* belongs to another
        workspace — the tenancy convention of this repository, where a resource the caller
        may not see is absent rather than forbidden, so that a 404 does not confirm an id.
        Raises :class:`~accretion.routing.train.RouterNotEvaluatedError` when the candidate
        has no holdout evaluation on record (AC4-M4-016 at its second door), and
        :class:`ShadowRegistrationError` when the version is not a CANDIDATE.

        Idempotent by construction rather than by key: the registered version's id is derived
        from the workspace, the candidate and the budget's digest, so registering the same
        candidate under the same budget twice returns the first row instead of writing a
        second one that the append-only store would refuse for having a later ``created_at``.
        A *different* budget is a different policy and gets a different id, which is the only
        distinction a reader of the two rows could act on.
        """

        candidate = await self.store.get_router_model_version(candidate_version_id)
        if candidate is None or candidate.workspace_id != workspace_id:
            raise KeyError(candidate_version_id)
        # Order matters: the evaluation gate runs before the lifecycle gate so that an
        # unevaluated version is refused for the reason AC4-M4-016 names, whichever status it
        # happens to carry.
        LearnedPredictorLoader.require_evaluated(candidate)
        if candidate.status is not RouterStatus.CANDIDATE:
            raise ShadowRegistrationError(
                f"router version {candidate.contract_id} is {candidate.status.value}; a "
                "shadow stage evaluates a CANDIDATE, and shadowing an already-promoted or "
                "retired version would either duplicate the live policy or resurrect a "
                "rollback target as a live experiment"
            )

        version_id = derived_id(
            "router_model_version",
            workspace_id,
            candidate.contract_id,
            RouterStatus.SHADOW.value,
            budget.digest(),
        )
        existing = await self.store.get_router_model_version(version_id)
        if existing is not None:
            return existing

        labels = {
            ACCEPTANCE_LABEL: candidate.labels[ACCEPTANCE_LABEL],
            CALIBRATION_REPORT_LABEL: candidate.labels[CALIBRATION_REPORT_LABEL],
            SHADOW_PARENT_LABEL: candidate.contract_id,
            **budget.labels(),
        }
        # Built through `model_validate` for the reason `RouterTrainingService.train` gives:
        # the pydantic mypy plugin does not carry `CanonicalContract`'s header fields onto a
        # subclass declared in another module.
        version = RouterModelVersion.model_validate(
            {
                "contract_id": version_id,
                "created_at": self.clock(),
                "created_by": principal,
                "workspace_id": workspace_id,
                "project_id": candidate.project_id,
                "scope": candidate.scope,
                "algorithm_id": candidate.algorithm_id,
                "feature_schema_version": candidate.feature_schema_version,
                "training_snapshot_id": candidate.training_snapshot_id,
                "artifact_digest": candidate.artifact_digest,
                "calibration_artifact_digest": candidate.calibration_artifact_digest,
                "parent_version_id": candidate.contract_id,
                "status": RouterStatus.SHADOW,
                "labels": labels,
            }
        )
        stored = await self.store.put_router_model_version(version)
        await self._announce(stored, candidate, run_id=run_id)
        return stored

    async def _announce(
        self,
        version: RouterModelVersion,
        candidate: RouterModelVersion,
        *,
        run_id: str | None,
    ) -> None:
        """Emit the registration against a run, when there is a run to emit it against.

        The same rule and the same reason as
        :meth:`~accretion.routing.train.RouterTrainingService._announce`: ``AgentEvent``
        requires a ``run_id`` and ``PostgresStore.append_event`` raises ``KeyError`` when the
        run does not exist, so a synthesised id would invent a run that never executed.

        ``normalized_type`` is ``ROUTER_CANDIDATE_TRAINED`` because §12's twelve v0.4 event
        types were declared once, in M1, and none of them names the shadow stage; the native
        type carries the distinction, which is exactly what ``native_type`` is for. Widening
        the enum here would edit a frozen contract and regenerate the TypeScript schema for
        one string.
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
                native_type="router.shadow.registered",
                normalized_type=EventType.ROUTER_CANDIDATE_TRAINED,
                payload={
                    "shadow_version_id": version.contract_id,
                    "candidate_version_id": candidate.contract_id,
                    ACCEPTANCE_LABEL: version.labels[ACCEPTANCE_LABEL],
                    **{key: value for key, value in sorted(version.labels.items())
                       if key.startswith(SHADOW_BUDGET_LABEL_PREFIX)},
                },
                adapter_version=SHADOW_ADAPTER_VERSION,
            )
        )


def _projected_utility(receipt: RoutingDecisionReceipt) -> float:
    """The receipt's own projection of run-verified success, or ``0.0`` if it made none.

    Quality-only, and deliberately: ``ShadowDecision.projected_utility_delta`` is written at
    the moment the two decisions are compared, and neither the receipt nor its embedded
    objective reference carries a :class:`~accretion.contracts.routing.UtilityWeights` vector
    to price cost and latency against. The weighted comparison is the *observed* one, which
    :func:`paired_deltas` computes from rollouts and real weights; recording a weighted number
    here would mean inventing the weights, and a fabricated projection is worse than a
    narrower honest one.

    A decision that selected nothing (``HUMAN_REVIEW_REQUIRED`` is the only type permitted
    to) has no predicted outcomes and projects ``0.0``.
    """

    if receipt.predicted_outcomes is None:
        return 0.0
    return receipt.predicted_outcomes.run_verified_success.mean


async def record_shadow_decision(
    store: StateStore,
    *,
    executed_receipt: RoutingDecisionReceipt,
    shadow_receipt: RoutingDecisionReceipt,
    version_id: str,
    notes: str,
) -> ShadowDecision:
    """Record what the shadow version would have chosen beside what was executed.

    Both receipts are read-only inputs. Nothing is dispatched, nothing is overridden and the
    executed receipt is not written back — a shadow decision that could affect execution
    would not be a shadow, and the absence of any write to ``routing_receipts`` here is the
    mechanical form of that sentence.

    ``agreement`` is ``True`` exactly when both receipts selected a configuration and the two
    ``selected_configuration_hash`` values are equal. Two decisions that both selected nothing
    have not agreed on anything, which is the rule
    :class:`~accretion.contracts.routing.ShadowDecision` enforces on the way in; computing the
    flag here from the same two hashes the record stores means the two can never disagree.

    Raises ``ValueError`` when the two receipts are the same record, which the contract also
    refuses — a comparison needs two decisions.
    """

    executed_hash = executed_receipt.selected_configuration_hash
    shadow_hash = shadow_receipt.selected_configuration_hash
    agreement = executed_hash is not None and executed_hash == shadow_hash
    decision = ShadowDecision.model_validate(
        {
            "contract_id": derived_id(
                "shadow_decision",
                executed_receipt.contract_id,
                shadow_receipt.contract_id,
                version_id,
            ),
            "created_at": executed_receipt.created_at,
            "created_by": executed_receipt.created_by,
            "workspace_id": executed_receipt.workspace_id,
            "project_id": executed_receipt.project_id,
            "executed_receipt_id": executed_receipt.contract_id,
            "shadow_receipt_id": shadow_receipt.contract_id,
            "shadow_router_version_id": version_id,
            "executed_configuration_hash": executed_hash,
            "shadow_configuration_hash": shadow_hash,
            "agreement": agreement,
            "projected_utility_delta": round(
                _projected_utility(shadow_receipt) - _projected_utility(executed_receipt), 6
            ),
            "comparison_notes": notes,
            "evaluated_at": shadow_receipt.created_at,
        }
    )
    return await store.put_shadow_decision(decision)


@dataclass(frozen=True, slots=True)
class PairedDelta:
    """One complete ``(SHADOW, CONTROL)`` rollout pair and the utility difference it measured.

    ``delta`` is ``U(shadow) - U(control)``, the sign convention ADR-060 states: positive
    means the candidate router's configuration did better on this trial. The two row ids are
    carried so a reported number can be traced back to the two forks that produced it rather
    than to an aggregate.
    """

    shadow_decision_id: str
    trial_index: int
    shadow_result_id: str
    control_result_id: str
    shadow_utility: float
    control_utility: float
    delta: float


def _outcome_of(result: ShadowRolloutResult, *, latency_budget_ms: float) -> Outcome:
    """One rollout row as the utility function's :class:`~accretion.routing.regret.Outcome`.

    ``latency`` is clamped at 1.0 for the reason the benchmark clamps it: past the budget the
    run is late, and how late stops changing the decision while it would otherwise dominate a
    weighted sum. ``invalid`` is ``False`` because a rollout row exists only for a fork that
    ran; a configuration the node refused produces no fork and therefore no row.
    """

    return Outcome(
        quality=result.observed.quality,
        cost=result.observed.cost,
        latency=min(1.0, result.observed.latency_ms / latency_budget_ms),
        latency_ms=int(result.observed.latency_ms),
        verified=result.observed.verified,
        false_accept=bool(result.observed.false_accept),
        invalid=False,
    )


def paired_deltas(
    results: Sequence[ShadowRolloutResult],
    *,
    weights: UtilityWeights,
    latency_budget_ms: float = DEFAULT_LATENCY_BUDGET_MS,
) -> list[PairedDelta]:
    """The complete rollout pairs, in ``(shadow_decision_id, trial_index)`` order.

    Rows are joined by ``(shadow_decision_id, trial_index)`` — the trial index is part of the
    key and not a detail, because one shadow decision is branched repeatedly and pairing on
    the decision alone would cross trial 0's shadow arm with trial 3's control. A group that
    does not hold exactly one ``SHADOW`` row and one ``CONTROL`` row is **dropped**: a lone
    arm measures the node's difficulty rather than the router's contribution, and scoring it
    against the other arm's pooled mean would put that difficulty back into the estimate with
    the opposite sign. A duplicated arm is dropped for the same reason in reverse — two rows
    claiming to be the same fork of the same trial cannot both be it, and picking one would be
    picking by iteration order.

    ``latency_budget_ms`` is keyword-only with a default so that the frozen two-argument call
    ``paired_deltas(rows, weights=w)`` keeps working; pass the node's
    ``ResourceBudget.maximum_latency_ms`` when the caller has it.
    """

    grouped: dict[tuple[str, int], dict[ShadowRolloutKind, list[ShadowRolloutResult]]] = {}
    for result in results:
        key = (result.shadow_decision_id, result.trial_index)
        grouped.setdefault(key, {}).setdefault(result.kind, []).append(result)

    paired: list[PairedDelta] = []
    for (decision_id, trial_index), arms in sorted(grouped.items()):
        shadow_arm = arms.get(ShadowRolloutKind.SHADOW, [])
        control_arm = arms.get(ShadowRolloutKind.CONTROL, [])
        if len(shadow_arm) != 1 or len(control_arm) != 1:
            continue
        shadow_row, control_row = shadow_arm[0], control_arm[0]
        shadow_utility = utility(
            _outcome_of(shadow_row, latency_budget_ms=latency_budget_ms), weights
        )
        control_utility = utility(
            _outcome_of(control_row, latency_budget_ms=latency_budget_ms), weights
        )
        paired.append(
            PairedDelta(
                shadow_decision_id=decision_id,
                trial_index=trial_index,
                shadow_result_id=shadow_row.contract_id,
                control_result_id=control_row.contract_id,
                shadow_utility=shadow_utility,
                control_utility=control_utility,
                delta=round(shadow_utility - control_utility, 6),
            )
        )
    return paired


class ShadowPair(StrictModel):
    """One shadow decision as the report shows it, with its rollout pair when there is one.

    There is one of these per ``(decision, trial)`` that produced a complete pair, and one
    per decision that produced none — with ``observed_delta`` and both result ids ``None``.
    Incomplete decisions are listed rather than filtered because the M9b UI narrows the report
    to a single run, and a run whose shadow forks all failed must look different from a run
    that was never shadowed at all.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    executed_receipt_id: str = Field(min_length=1, max_length=64)
    shadow_receipt_id: str = Field(min_length=1, max_length=64)
    agreement: bool
    projected_utility_delta: float
    control_result_id: str | None = Field(default=None, min_length=1, max_length=64)
    shadow_result_id: str | None = Field(default=None, min_length=1, max_length=64)
    observed_delta: float | None = None

    @model_validator(mode="after")
    def _an_observed_delta_names_both_forks(self) -> Self:
        complete = self.control_result_id is not None and self.shadow_result_id is not None
        if (self.observed_delta is not None) != complete:
            raise ValueError(
                "an observed delta is the difference between two named forks; this pair has "
                f"observed_delta={self.observed_delta!r} with control_result_id="
                f"{self.control_result_id!r} and shadow_result_id={self.shadow_result_id!r}"
            )
        return self


class GateStatus(StrictModel):
    """One promotion precondition, whether it is met, and the number that decided it.

    ``evidence`` is a rendered string rather than a float because the three gates are not
    measured in the same unit and a shared numeric field would need a second field saying
    which unit it was in. The string is what an operator reads and what M8.2 quotes into a
    promotion report's refusal.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    gate: str = Field(min_length=1, max_length=64)
    met: bool
    evidence: str = Field(min_length=1, max_length=1_000)


class ShadowReportConfig(StrictModel):
    """The constants a shadow report is computed under, so that two readers agree.

    Every one of them changes the answer, and none of them may be chosen after the rows are
    seen: the weights price the three observed quantities against each other, the seed and the
    replicate count fix the interval, and the two thresholds decide the gates. Holding them in
    one sealed object rather than as six defaulted parameters is what lets M6.2's route and
    M8.2's promotion gate demonstrate they used the same ones.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    seed: int = Field(ge=0)
    bootstraps: int = Field(default=DEFAULT_BOOTSTRAPS, ge=1)
    alpha: float = Field(default=DEFAULT_ALPHA, gt=0, lt=1)
    latency_budget_ms: float = Field(default=DEFAULT_LATENCY_BUDGET_MS, gt=0)
    min_paired_runs: int = Field(default=DEFAULT_MIN_PAIRED_RUNS, ge=1)
    delta_ni: float = DEFAULT_DELTA_NI


class ShadowReport(StrictModel):
    """What a shadow stage has shown so far, and what it still owes a promotion.

    M6.2 returns this unchanged as a route's ``response_model`` and M8.2 feeds it to
    :func:`shadow_gate`, which is why it carries both the aggregate and every pair behind it:
    a report that quoted only ``mean_delta`` would be a number an operator has to trust, and
    ``pairs`` is what makes it a number they can recompute.

    ``mean_delta`` is the unweighted mean over complete pairs and ``delta_lcb`` the lower end
    of the decision-clustered bootstrap interval on that mean. With no complete pairs both are
    ``0.0`` and ``non_inferior`` is ``False``: an empty sample is not a null result, and a
    report that returned "non-inferior, mean 0.0" for zero measurements would be an assertion
    made from nothing.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    version_id: str = Field(min_length=1, max_length=64)
    paired_count: int = Field(ge=0)
    agreement_rate: float = Field(ge=0, le=1)
    mean_delta: float
    delta_lcb: float
    non_inferior: bool
    pairs: list[ShadowPair] = Field(default_factory=list)
    remaining_gates: list[GateStatus] = Field(default_factory=list)


PAIRED_RUNS_GATE = "paired_runs"
"""The gate that counts complete pairs against ``min_paired_runs``."""

NON_INFERIORITY_GATE = "non_inferiority"
"""The gate that compares ``delta_lcb`` against ``delta_ni``."""


def shadow_report(
    results: Sequence[ShadowRolloutResult],
    decisions: Sequence[ShadowDecision],
    *,
    weights: UtilityWeights,
    config: ShadowReportConfig,
) -> ShadowReport:
    """Score one shadow version's decisions and rollouts into a report.

    ``decisions`` must all name the same ``shadow_router_version_id``; the report is about one
    version and pooling two would average two policies into a verdict about neither. Rollout
    rows whose ``shadow_decision_id`` is not among the decisions are ignored rather than
    raised on, because the caller lists a workspace's rows and a workspace holds more than one
    shadow stage.

    Raises ``ValueError`` for an empty ``decisions`` list or for decisions naming more than
    one version: both are questions with no answer, and returning a zeroed report for them
    would let a caller mistake "nothing was shadowed" for "the shadow was neutral".
    """

    if not decisions:
        raise ValueError(
            "a shadow report needs at least one shadow decision; there is no report to make "
            "about a version that was never asked what it would have chosen"
        )
    version_ids = {decision.shadow_router_version_id for decision in decisions}
    if len(version_ids) != 1:
        raise ValueError(
            f"the decisions name {len(version_ids)} shadow router versions "
            f"{sorted(version_ids)!r}; a report describes one policy, and averaging two "
            "would produce a verdict about neither"
        )
    version_id = version_ids.pop()

    by_id = {decision.contract_id: decision for decision in decisions}
    relevant = [row for row in results if row.shadow_decision_id in by_id]
    deltas = paired_deltas(
        relevant, weights=weights, latency_budget_ms=config.latency_budget_ms
    )
    by_decision: dict[str, list[PairedDelta]] = {}
    for pair in deltas:
        by_decision.setdefault(pair.shadow_decision_id, []).append(pair)

    pairs: list[ShadowPair] = []
    for decision in sorted(decisions, key=lambda item: item.contract_id):
        matched = by_decision.get(decision.contract_id, [])
        if not matched:
            pairs.append(
                ShadowPair(
                    executed_receipt_id=decision.executed_receipt_id,
                    shadow_receipt_id=decision.shadow_receipt_id,
                    agreement=decision.agreement,
                    projected_utility_delta=decision.projected_utility_delta,
                )
            )
            continue
        for pair in matched:
            pairs.append(
                ShadowPair(
                    executed_receipt_id=decision.executed_receipt_id,
                    shadow_receipt_id=decision.shadow_receipt_id,
                    agreement=decision.agreement,
                    projected_utility_delta=decision.projected_utility_delta,
                    control_result_id=pair.control_result_id,
                    shadow_result_id=pair.shadow_result_id,
                    observed_delta=pair.delta,
                )
            )

    paired_count = len(deltas)
    agreement_rate = round(
        sum(1 for decision in decisions if decision.agreement) / len(decisions), 6
    )
    if deltas:
        mean_delta = round(sum(pair.delta for pair in deltas) / paired_count, 6)
        # Clustered by shadow decision: several trials of one decision are several looks at
        # one node under one seed policy, and resampling them as independent units would
        # narrow the interval by about the square root of the cluster size.
        groups = {
            decision_id: [pair.delta for pair in group]
            for decision_id, group in by_decision.items()
        }
        lower, _ = hierarchical_bootstrap(
            groups,
            lambda values: sum(values) / len(values),
            config.bootstraps,
            config.seed,
            config.alpha,
        )
        delta_lcb = round(lower, 6)
        non_inferior = delta_lcb >= config.delta_ni
    else:
        mean_delta = 0.0
        delta_lcb = 0.0
        non_inferior = False

    return ShadowReport(
        version_id=version_id,
        paired_count=paired_count,
        agreement_rate=agreement_rate,
        mean_delta=mean_delta,
        delta_lcb=delta_lcb,
        non_inferior=non_inferior,
        pairs=pairs,
        remaining_gates=[
            GateStatus(
                gate=PAIRED_RUNS_GATE,
                met=paired_count >= config.min_paired_runs,
                evidence=(
                    f"{paired_count} complete pairs of the {config.min_paired_runs} required "
                    f"({len(decisions)} shadow decisions recorded)"
                ),
            ),
            GateStatus(
                gate=NON_INFERIORITY_GATE,
                met=non_inferior,
                evidence=(
                    f"delta_lcb {delta_lcb} against a floor of {config.delta_ni} "
                    f"(mean {mean_delta} over {paired_count} pairs, "
                    f"seed {config.seed}, B={config.bootstraps})"
                ),
            ),
        ],
    )


def shadow_gate(
    report: ShadowReport, *, min_paired_runs: int, delta_ni: float
) -> bool:
    """Whether ``report`` clears the shadow stage. Both conditions, never one.

    The thresholds are arguments rather than read off the report, so that M8.2's promotion
    gate states the bar it is applying instead of inheriting whatever the report was rendered
    under. Passing the same values the report used reproduces its ``remaining_gates`` exactly;
    passing stricter ones is how a promotion asks for more than a dashboard did.

    A report with fewer than ``min_paired_runs`` complete pairs is refused *before* its
    interval is consulted: a lower bound computed from four pairs can clear any floor, and
    reporting it as a pass would make the count gate decorative.
    """

    if report.paired_count < min_paired_runs:
        return False
    return report.delta_lcb >= delta_ni


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_BOOTSTRAPS",
    "DEFAULT_DELTA_NI",
    "DEFAULT_LATENCY_BUDGET_MS",
    "DEFAULT_MIN_PAIRED_RUNS",
    "NON_INFERIORITY_GATE",
    "PAIRED_RUNS_GATE",
    "SHADOW_ADAPTER_VERSION",
    "SHADOW_BUDGET_LABEL_PREFIX",
    "SHADOW_PARENT_LABEL",
    "GateStatus",
    "PairedDelta",
    "ShadowBudget",
    "ShadowError",
    "ShadowEvaluator",
    "ShadowPair",
    "ShadowRegistrationError",
    "ShadowReport",
    "ShadowReportConfig",
    "paired_deltas",
    "record_shadow_decision",
    "shadow_gate",
    "shadow_report",
]
