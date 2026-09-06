"""SDD §15.3: the six conditions under which automatic exploration is disabled.

§15.3 is a list of six sentences, and this module is those six sentences and nothing else.
Each one is a **pure predicate** over a frozen :class:`BreakerInput` — no store, no clock, no
network, no live runtime — which is what makes them provable rather than merely reviewable:
a predicate over frozen inputs can be exercised on both sides of its own boundary, and the
composite can be exercised over every subset of the six.

**Why predicates and not a service.** A breaker that read the store would have to be tested
against a store, and the interesting cases (the rate is exactly at the ceiling; two breakers
trip at once) would be buried under fixtures. Sampling — turning recent verification
outcomes, calibration reports and promotion cohorts into a :class:`BreakerInput` — is
somebody else's job and happens above this module. What arrives here is already a decision's
worth of frozen numbers.

**The composite never short-circuits.** :func:`exploration_allowed` evaluates all six and
returns *every* tripped id, in :data:`BREAKERS` order. An operator reading "exploration is
off because the false-acceptance alert fired" and fixing only that, when calibration had also
blown, would turn exploration back on into a second unsafe condition. The cost of evaluating
six pure predicates is nothing; the cost of a half-told reason is another incident.

**Fail closed, twice.** A cohort with a lower confidence bound but no recorded baseline
trips :func:`critical_cohort_regression`: a cohort that cannot be compared has not been shown
not to have regressed. A serving version with no recorded validated window trips
:func:`unvalidated_version_drift` (OQ-415) for the same reason — "unvalidated" is precisely
what this breaker is named after. Neither case raises: a breaker that raised mid-decision
would take exploration and the deterministic baseline down together.

**Boundaries are inclusive of the healthy side.** A ceiling is the highest tolerated value,
so ``rate == ceiling`` does not trip; a floor is the lowest tolerated value, so
``coverage == floor`` does not trip; and a validated version window includes its endpoints.
Every threshold in this module is stated that way, and each is tested from both sides.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class BreakerInput:
    """One decision's worth of evidence, frozen, with everything six predicates need.

    Every field is a plain number, boolean or mapping rather than a contract, because a
    breaker asks a question about *recent aggregate behaviour* and no single sealed contract
    holds that. The sampler that builds this is free to derive the fields from
    ``IndependentVerificationResult`` rows, a ``RouterPromotionReport``'s cohorts, an
    availability probe or anything else; the predicates below cannot tell and must not care.

    ``cohort_lcbs`` maps a §10.2 cohort id to the lower confidence bound on that cohort's
    metric under the configuration exploration would use; ``cohort_baselines`` maps the same
    ids to the baseline's point value, and ``delta_ni`` is the non-inferiority margin allowed
    below it. ``serving_versions`` maps a component id (a provider, a model, a verifier) to
    the version currently serving, and ``version_boundaries`` maps it to the inclusive
    ``(low, high)`` window that was validated for it.

    Rates, bounds, margins and coverage are all normalised fractions in ``[0, 1]``; nothing
    here is a percentage and nothing here is a count.
    """

    false_acceptance_rate_recent: float
    false_acceptance_ceiling: float
    ece_recent: float
    max_ece: float
    cohort_lcbs: Mapping[str, float]
    cohort_baselines: Mapping[str, float]
    delta_ni: float
    serving_versions: Mapping[str, str]
    version_boundaries: Mapping[str, tuple[str, str]]
    verification_coverage_recent: float
    coverage_floor: float
    policy_snapshot_resolved: bool
    audit_probe_ok: bool


@dataclass(frozen=True, slots=True)
class BreakerVerdict:
    """What one breaker concluded, and why, in words an operator can act on.

    ``detail`` is populated on both outcomes and never empty. A verdict that explained itself
    only when it tripped would make the healthy case unauditable: "exploration was allowed"
    is a claim about six numbers, and a receipt that recorded the claim without the numbers
    could not be replayed against them.
    """

    tripped: bool
    breaker_id: str
    detail: str


Breaker = Callable[[BreakerInput], BreakerVerdict]
"""The shape all six share: one frozen input in, one explained verdict out."""


def false_acceptance_alert(inputs: BreakerInput) -> BreakerVerdict:
    """§15.3, first condition. Trips when the recent false-acceptance rate is over ceiling.

    The ceiling is the highest rate the ``ObjectiveContract`` tolerates, so being exactly at
    it is tolerated and does not trip.
    """
    rate = inputs.false_acceptance_rate_recent
    ceiling = inputs.false_acceptance_ceiling
    if rate > ceiling:
        return BreakerVerdict(
            tripped=True,
            breaker_id="false_acceptance_alert",
            detail=(
                f"recent false-acceptance rate {rate} exceeds the objective's ceiling "
                f"{ceiling}"
            ),
        )
    return BreakerVerdict(
        tripped=False,
        breaker_id="false_acceptance_alert",
        detail=f"recent false-acceptance rate {rate} is within the ceiling {ceiling}",
    )


def calibration_exceeded(inputs: BreakerInput) -> BreakerVerdict:
    """§15.3, second condition. Trips when expected calibration error is over its maximum.

    Exploration is chosen against predicted outcomes and their confidence bounds. A router
    whose calibration has drifted is still producing bounds, and they are still numbers, and
    they no longer mean what the exploration gate reads them as meaning.
    """
    ece = inputs.ece_recent
    maximum = inputs.max_ece
    if ece > maximum:
        return BreakerVerdict(
            tripped=True,
            breaker_id="calibration_exceeded",
            detail=f"recent expected calibration error {ece} exceeds the maximum {maximum}",
        )
    return BreakerVerdict(
        tripped=False,
        breaker_id="calibration_exceeded",
        detail=f"recent expected calibration error {ece} is within the maximum {maximum}",
    )


def critical_cohort_regression(inputs: BreakerInput) -> BreakerVerdict:
    """§15.3, third condition. Trips when any cohort's LCB falls below ``baseline − Δ_NI``.

    Every cohort in ``cohort_lcbs`` is treated as critical: the sampler decides which §10.2
    cohorts are critical (``CohortResult.critical``) and passes only those, which keeps
    OQ-413's open list of critical cohorts out of this module entirely.

    A cohort with no recorded baseline trips. Non-inferiority is a comparison, and a cohort
    that cannot be compared has not been shown not to have regressed.

    Cohort ids are walked in sorted order and *all* regressions are named, so the detail is
    the same string for the same input regardless of how the mappings were built.
    """
    margin = inputs.delta_ni
    regressed: list[str] = []
    for cohort_id in sorted(inputs.cohort_lcbs):
        lcb = inputs.cohort_lcbs[cohort_id]
        if cohort_id not in inputs.cohort_baselines:
            regressed.append(f"{cohort_id} (lcb {lcb}, no recorded baseline)")
            continue
        baseline = inputs.cohort_baselines[cohort_id]
        if lcb < baseline - margin:
            regressed.append(f"{cohort_id} (lcb {lcb} below {baseline} - {margin})")
    if regressed:
        return BreakerVerdict(
            tripped=True,
            breaker_id="critical_cohort_regression",
            detail="critical cohorts regressed: " + ", ".join(regressed),
        )
    return BreakerVerdict(
        tripped=False,
        breaker_id="critical_cohort_regression",
        detail=(
            f"all {len(inputs.cohort_lcbs)} critical cohorts hold their baseline within the "
            f"non-inferiority margin {margin}"
        ),
    )


_VERSION_SEPARATOR: Final = re.compile(r"[._+-]")


_VersionKey = tuple[tuple[int, ...], int, tuple[str, ...]]
"""A version as its release numbers, whether it is a final release, and its prerelease tail."""


def _version_key(version: str) -> _VersionKey:
    """Order a version: release numbers numerically, and a prerelease below its own release.

    ``2.10.0`` is above ``2.4.0`` and a lexicographic comparison would say the opposite,
    which is the first reason this exists. The second is prereleases. Segments split on
    ``.``, ``_``, ``-`` and ``+``; the leading run of entirely-numeric segments is the
    release, and the first non-numeric segment together with everything after it is the
    prerelease tail.

    **A prerelease sorts below the release it precedes.** ``2.0.0-rc1`` is below ``2.0.0``,
    so a release candidate serving against a window whose low endpoint is the GA version it
    precedes falls outside that window and trips :func:`unvalidated_version_drift`. Ordering
    it the other way — as a longer, and therefore greater, version than its own prefix —
    would seat an unvalidated rc *inside* a window validated only for the GA releases either
    side of it, admitting in silence the exact drift this breaker is named after.

    Two consequences are deliberate. A version with more release segments than a boundary is
    above it, so ``4.0.0`` against the window ``("4.0", "4.0")`` trips: a boundary recorded in
    a different shape was not recorded for that version. And prerelease tails compare
    lexically among themselves rather than by full semver precedence, so ``rc.10`` sits below
    ``rc.2``; that errs toward tripping and never toward admitting an uncompared version.

    This is an ordering for a validated *window*: total, deterministic and dependency-free,
    and the boundaries it compares against are recorded by the same process that records the
    serving version.
    """
    release: list[int] = []
    prerelease: list[str] = []
    for segment in _VERSION_SEPARATOR.split(version.strip()):
        if not segment:
            continue
        if prerelease or not segment.isdigit():
            prerelease.append(segment)
        else:
            release.append(int(segment))
    return tuple(release), 0 if prerelease else 1, tuple(prerelease)


def unvalidated_version_drift(inputs: BreakerInput) -> BreakerVerdict:
    """§15.3, fourth condition (OQ-415). Trips on a serving version outside its window.

    The window is inclusive at both ends: its endpoints are versions that *were* validated.
    A serving component with no recorded window trips, because a version nobody recorded a
    validation for is the definition of unvalidated drift. A prerelease of a validated
    release is likewise not that release: ``2.0.0-rc1`` is outside a window whose low
    endpoint is ``2.0.0``, because only the GA release was validated (see
    :func:`_version_key`).

    Components are walked in sorted order and all drifting ones are named.
    """
    drifted: list[str] = []
    for component in sorted(inputs.serving_versions):
        serving = inputs.serving_versions[component]
        window = inputs.version_boundaries.get(component)
        if window is None:
            drifted.append(f"{component} serving {serving} with no validated window")
            continue
        low, high = window
        serving_key = _version_key(serving)
        if serving_key < _version_key(low) or serving_key > _version_key(high):
            drifted.append(f"{component} serving {serving} outside [{low}, {high}]")
    if drifted:
        return BreakerVerdict(
            tripped=True,
            breaker_id="unvalidated_version_drift",
            detail="unvalidated version drift: " + ", ".join(drifted),
        )
    return BreakerVerdict(
        tripped=False,
        breaker_id="unvalidated_version_drift",
        detail=(
            f"all {len(inputs.serving_versions)} serving versions are inside their validated "
            "windows"
        ),
    )


def verification_coverage_drop(inputs: BreakerInput) -> BreakerVerdict:
    """§15.3, fifth condition. Trips when recent verification coverage is under the floor.

    The floor is the lowest coverage that still makes an exploration's outcome evidence, so
    being exactly at it is tolerated and does not trip.
    """
    coverage = inputs.verification_coverage_recent
    floor = inputs.coverage_floor
    if coverage < floor:
        return BreakerVerdict(
            tripped=True,
            breaker_id="verification_coverage_drop",
            detail=f"recent verification coverage {coverage} is below the floor {floor}",
        )
    return BreakerVerdict(
        tripped=False,
        breaker_id="verification_coverage_drop",
        detail=f"recent verification coverage {coverage} holds the floor {floor}",
    )


def policy_or_audit_unavailable(inputs: BreakerInput) -> BreakerVerdict:
    """§15.3, sixth condition. Trips when the policy snapshot or the audit probe is missing.

    Both are named when both are missing rather than reporting the first: the two have
    different owners and restoring one does not restore the other.
    """
    missing: list[str] = []
    if not inputs.policy_snapshot_resolved:
        missing.append("the policy snapshot did not resolve")
    if not inputs.audit_probe_ok:
        missing.append("the audit service probe failed")
    if missing:
        return BreakerVerdict(
            tripped=True,
            breaker_id="policy_or_audit_unavailable",
            detail="; ".join(missing),
        )
    return BreakerVerdict(
        tripped=False,
        breaker_id="policy_or_audit_unavailable",
        detail="the policy snapshot resolved and the audit service probe succeeded",
    )


BREAKERS: Final[tuple[Breaker, ...]] = (
    false_acceptance_alert,
    calibration_exceeded,
    critical_cohort_regression,
    unvalidated_version_drift,
    verification_coverage_drop,
    policy_or_audit_unavailable,
)
"""The six of §15.3, in the order §15.3 lists them.

The order is not arbitrary decoration: it is the order tripped ids are reported in, so an
operator comparing two incidents is comparing two lists built the same way, and a report
generated a month apart from the same evidence is byte-identical.
"""


def exploration_allowed(inputs: BreakerInput) -> tuple[bool, list[str]]:
    """Evaluate all six. Automatic exploration is allowed only when none of them tripped.

    Returns the decision and the ids of every tripped breaker in :data:`BREAKERS` order —
    every one, always, because the caller is going to act on the list and a list that stopped
    at the first hit would send them back into the second condition.
    """
    tripped = [
        verdict.breaker_id for verdict in (breaker(inputs) for breaker in BREAKERS)
        if verdict.tripped
    ]
    return not tripped, tripped
